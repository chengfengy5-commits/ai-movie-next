import { isSafeCoverUrl } from "../../shared/api/config";
import type { Chapter, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";

export type TextField =
  | { kind: "value"; value: string }
  | { kind: "missing" }
  | { kind: "invalid" };

export interface StoryboardFrameView {
  position: number;
  text: TextField;
  originalText: TextField;
  referenceCount: number;
  hasPreview: boolean;
  imageUrl: string | null;
  imageStatus: "matched" | "missing" | "unavailable";
}

function projectText(value: unknown): TextField {
  if (value === undefined || value === null) {
    return { kind: "missing" };
  }
  if (typeof value !== "string") {
    return { kind: "invalid" };
  }
  return { kind: "value", value };
}

function referenceCount(frame: StoryboardFrame): number {
  return ["character", "scene", "prop"].reduce((count, key) => {
    const value = frame[key];
    return count + (Array.isArray(value)
      ? value.filter((reference) => typeof reference === "string" && reference.trim() !== "").length
      : 0);
  }, 0);
}

function storyboardId(frame: StoryboardFrame): string | null {
  const value = frame.storyboard;
  if (!Array.isArray(value) || typeof value[0] !== "string" || value[0].trim() === "") {
    return null;
  }
  return value[0];
}

export function projectStoryboardFrames(
  chapter: Chapter,
  assets: StoryboardAsset[],
  apiBaseUrl: string | null,
): StoryboardFrameView[] {
  const frames = chapter.content ?? [];
  const identities = frames.map(storyboardId);
  const identityCounts = new Map<string, number>();
  for (const identity of identities) {
    if (identity !== null) {
      identityCounts.set(identity, (identityCounts.get(identity) ?? 0) + 1);
    }
  }

  return frames.map((frame, index) => {
    const identity = identities[index] ?? null;
    let imageUrl: string | null = null;
    let imageStatus: StoryboardFrameView["imageStatus"] = "unavailable";

    if (identity !== null && identityCounts.get(identity) === 1) {
      const sameIdentityAssets = assets.filter((asset) => asset.id === identity);
      if (
        sameIdentityAssets.length === 1
        && sameIdentityAssets[0]?.series_id === chapter.series_id
        && sameIdentityAssets[0]?.chapter_id === chapter.id
      ) {
        const asset = sameIdentityAssets[0];
        const safeUrl = apiBaseUrl === null || asset === undefined
          ? null
          : isSafeCoverUrl(asset.image_url, apiBaseUrl);
        if (safeUrl !== null) {
          imageUrl = safeUrl;
          imageStatus = "matched";
        } else {
          imageStatus = asset.image_url === null || asset.image_url.trim() === ""
            ? "missing"
            : "unavailable";
        }
      }
    }

    return {
      position: index + 1,
      text: projectText(frame.text),
      originalText: projectText(frame.original_text),
      referenceCount: referenceCount(frame),
      hasPreview: typeof frame.preview === "string" && frame.preview.trim() !== "",
      imageUrl,
      imageStatus,
    };
  });
}
