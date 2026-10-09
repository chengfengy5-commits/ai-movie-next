import type { Chapter, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import type { PersonalProductionFrameSnapshot, PersonalProductionSnapshot } from "../../shared/api/personalProductionNotes";
import { normalizePersonalProductionMediaIdentity } from "./personal-production/mediaIdentity";

const knownPreviewDigests = new Map([
  ["https://preview.example.invalid/opening", "48840f1fcc675bb43f82b952d0d71d37ca7923cdc1d5d3c392f08e5e9e63b896"],
  ["preview-ready", "0f2f30360933cf70bed328f2ccb2767ec49687e81e5738a0698321b4dbc69504"],
]);

function storyboardIdentity(frame: StoryboardFrame): string | null {
  const storyboard = frame.storyboard;
  if (!Array.isArray(storyboard)) {
    return null;
  }
  const identity = storyboard[0];
  return typeof identity === "string" && identity.trim() !== "" ? identity : null;
}

function digestForDemoIdentity(value: string | null): { digest: string | null; valid: boolean } {
  const identity = normalizePersonalProductionMediaIdentity(value);
  if (identity === null) {
    return { digest: null, valid: true };
  }
  const digest = knownPreviewDigests.get(identity);
  return digest === undefined ? { digest: null, valid: false } : { digest, valid: true };
}

function demoFrameSnapshot(
  frame: StoryboardFrame,
  frameIndex: number,
  chapter: Chapter,
  assets: StoryboardAsset[],
): { value: PersonalProductionFrameSnapshot; candidateId: string | null } {
  const candidateId = storyboardIdentity(frame);
  const matchingAssets = candidateId === null
    ? []
    : assets.filter((asset) => asset.id === candidateId);
  const asset = matchingAssets.length === 1
    && matchingAssets[0]?.series_id === chapter.series_id
    && matchingAssets[0]?.chapter_id === chapter.id
    ? matchingAssets[0]
    : undefined;
  const assetDigest = digestForDemoIdentity(typeof asset?.image_url === "string" ? asset.image_url : null);
  const previewDigest = digestForDemoIdentity(typeof frame.preview === "string" ? frame.preview : null);
  const sourceValid = asset !== undefined && assetDigest.valid && previewDigest.valid;
  return {
    candidateId,
    value: {
      frame_index: frameIndex,
      storyboard_asset_id: sourceValid ? candidateId : null,
      media_revision: sourceValid ? (frameIndex === 0 ? 2 : 1) : null,
      source_valid: sourceValid,
      asset_image_digest: sourceValid ? assetDigest.digest : null,
      preview_digest: sourceValid ? previewDigest.digest : null,
      invalid_reason: sourceValid ? null : "素材身份无法核对。",
    },
  };
}

export function getDemoPersonalProductionNotes(
  chapterId: string,
  chapter: Chapter,
  assets: StoryboardAsset[],
): PersonalProductionSnapshot {
  const mediaState: PersonalProductionSnapshot["media_state"] = chapter.content === null
    ? "unreadable"
    : chapter.content.length === 0
      ? "empty"
      : "ready";
  const frames = mediaState === "ready"
    ? chapter.content!.map((frame, index) => demoFrameSnapshot(frame, index, chapter, assets))
    : [];
  const opening = chapter.title.startsWith("第一章");
  const frameNotes = new Map<string, unknown>();
  let resumeFrameId: string | null = null;
  if (opening) {
    const firstId = frames[0]?.candidateId;
    const secondId = frames[1]?.candidateId;
    if (firstId !== null && firstId !== undefined) {
      frameNotes.set(firstId, {
        status: "approved",
        note: "旧版本曾确认，原图版本变化后需要重新确认。",
        approved_media_revision: 1,
        needs_reconfirmation: false,
      });
      resumeFrameId = firstId;
    }
    if (secondId !== null && secondId !== undefined) {
      frameNotes.set(secondId, {
        status: "needs_revision",
        note: "信封封口处的雨痕需要补充表现。",
      });
    }
    frameNotes.set(chapter.series_id + "-retired-frame", {
      status: "needs_revision",
      note: "旧镜头记录，仅供参考。",
    });
  }

  return {
    chapter_id: chapterId,
    revision: opening ? 4 : 0,
    media_state: mediaState,
    frames: frames.map(({ value }) => value),
    frame_notes: frameNotes,
    resume_frame_id: resumeFrameId,
  };
}

export function clonePersonalProductionSnapshot(
  snapshot: PersonalProductionSnapshot,
): PersonalProductionSnapshot {
  return {
    ...snapshot,
    frames: snapshot.frames.map((frame) => ({ ...frame })),
    frame_notes: new Map([...snapshot.frame_notes.entries()].map(([id, value]) => [
      id,
      value !== null && typeof value === "object" && !Array.isArray(value)
        ? { ...value }
        : value,
    ])),
  };
}
