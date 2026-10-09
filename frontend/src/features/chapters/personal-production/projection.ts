import type { Chapter, StoryboardAsset, StoryboardFrame } from "../../../shared/api/contracts";
import {
  projectPersonalProductionNote,
  type PersonalProductionNoteProjection,
  type PersonalProductionNoteStatus,
  type PersonalProductionSnapshot,
} from "../../../shared/api/personalProductionNotes";
import { digestPersonalProductionMediaIdentity } from "./mediaIdentity";

export interface CapturedPersonalProductionFrame {
  frameIndex: number;
  candidateId: string | null;
  storyboardAssetId: string | null;
  identityValid: boolean;
  assetImageUrl: string | null;
  previewUrl: string | null;
  assetImageDigest: string | null;
  previewDigest: string | null;
  digestAvailable: boolean;
}

export interface CapturedPersonalProductionMedia {
  state: "ready" | "empty" | "unreadable";
  frames: CapturedPersonalProductionFrame[];
}

export type CurrentNoteStatus =
  | PersonalProductionNoteStatus
  | "needs_reconfirmation"
  | "unverifiable"
  | "unreadable";

export interface PersonalProductionFrameView {
  position: number;
  status: CurrentNoteStatus;
  note: string;
  hasSavedNote: boolean;
  verified: boolean;
}

export interface OrphanPersonalProductionNoteView {
  projection: PersonalProductionNoteProjection;
}

export interface PersonalProductionReadout {
  revision: number;
  noSavedRecord: boolean;
  mediaState: "ready" | "empty" | "unreadable" | "mismatch";
  frames: PersonalProductionFrameView[];
  orphanNotes: OrphanPersonalProductionNoteView[];
  resumePosition: number | null;
  resumeIsInvalid: boolean;
}

function storyboardIdentity(frame: StoryboardFrame): string | null {
  const storyboard = frame.storyboard;
  if (!Array.isArray(storyboard)) {
    return null;
  }
  const identity = storyboard[0];
  return typeof identity === "string" && identity.trim() !== "" ? identity : null;
}

function rawPreviewIdentity(frame: StoryboardFrame): string | null {
  return typeof frame.preview === "string" ? frame.preview : null;
}

export async function capturePersonalProductionMedia(
  chapter: Chapter,
  assets: StoryboardAsset[],
  digest: typeof digestPersonalProductionMediaIdentity = digestPersonalProductionMediaIdentity,
): Promise<CapturedPersonalProductionMedia> {
  if (chapter.content === null) {
    return { state: "unreadable", frames: [] };
  }
  if (chapter.content.length === 0) {
    return { state: "empty", frames: [] };
  }

  const candidates = chapter.content.map(storyboardIdentity);
  const candidateCounts = new Map<string, number>();
  for (const candidate of candidates) {
    if (candidate !== null) {
      candidateCounts.set(candidate, (candidateCounts.get(candidate) ?? 0) + 1);
    }
  }

  const frames = await Promise.all(chapter.content.map(async (frame, frameIndex) => {
    const candidateId = candidates[frameIndex] ?? null;
    const sameIdAssets = candidateId === null ? [] : assets.filter((asset) => asset.id === candidateId);
    const matches = sameIdAssets.filter((asset) => (
      asset.series_id === chapter.series_id && asset.chapter_id === chapter.id
    ));
    const identityValid = candidateId !== null
      && candidateCounts.get(candidateId) === 1
      && sameIdAssets.length === 1
      && matches.length === 1;
    const asset = identityValid ? matches[0] : undefined;
    const assetImageUrl = typeof asset?.image_url === "string" ? asset.image_url : null;
    const previewUrl = rawPreviewIdentity(frame);
    if (!identityValid) {
      return {
        frameIndex,
        candidateId,
        storyboardAssetId: null,
        identityValid: false,
        assetImageUrl: null,
        previewUrl,
        assetImageDigest: null,
        previewDigest: null,
        digestAvailable: false,
      };
    }

    try {
      const [assetImageDigest, previewDigest] = await Promise.all([
        digest(assetImageUrl),
        digest(previewUrl),
      ]);
      return {
        frameIndex,
        candidateId,
        storyboardAssetId: candidateId,
        identityValid: true,
        assetImageUrl,
        previewUrl,
        assetImageDigest,
        previewDigest,
        digestAvailable: true,
      };
    } catch {
      return {
        frameIndex,
        candidateId,
        storyboardAssetId: candidateId,
        identityValid: true,
        assetImageUrl,
        previewUrl,
        assetImageDigest: null,
        previewDigest: null,
        digestAvailable: false,
      };
    }
  }));
  return { state: "ready", frames };
}

function groupFramesByIdentity(snapshot: PersonalProductionSnapshot): Map<string, typeof snapshot.frames> {
  const grouped = new Map<string, typeof snapshot.frames>();
  for (const frame of snapshot.frames) {
    if (frame.storyboard_asset_id === null || frame.storyboard_asset_id === "") {
      continue;
    }
    const frames = grouped.get(frame.storyboard_asset_id) ?? [];
    frames.push(frame);
    grouped.set(frame.storyboard_asset_id, frames);
  }
  return grouped;
}

function noteStatus(
  projection: PersonalProductionNoteProjection,
  serverFrame: PersonalProductionSnapshot["frames"][number] | undefined,
  verified: boolean,
): { status: CurrentNoteStatus; note: string } {
  if (projection.kind === "unreadable") {
    return { status: "unreadable", note: "" };
  }
  const saved = projection.value;
  if (saved.status === "approved") {
    if (!verified || serverFrame === undefined) {
      return { status: "unverifiable", note: saved.note };
    }
    const hasMediaDigest = serverFrame.asset_image_digest !== null || serverFrame.preview_digest !== null;
    if (
      saved.needsReconfirmation
      || saved.approvedMediaRevision !== serverFrame.media_revision
      || !hasMediaDigest
      || serverFrame.media_revision === null
    ) {
      return { status: "needs_reconfirmation", note: saved.note };
    }
  }
  if (saved.needsReconfirmation) {
    return { status: "needs_reconfirmation", note: saved.note };
  }
  return { status: saved.status, note: saved.note };
}

export function projectPersonalProductionSnapshot(
  chapter: Chapter,
  captured: CapturedPersonalProductionMedia,
  snapshot: PersonalProductionSnapshot,
): PersonalProductionReadout {
  const localCount = captured.frames.length;
  let mediaState: PersonalProductionReadout["mediaState"] = captured.state;
  const chapterStateMatches = snapshot.chapter_id === chapter.id && snapshot.media_state === captured.state;
  const countMatches = snapshot.frames.length === localCount;
  const indexesMatch = snapshot.frames.every((frame, index) => frame.frame_index === index);
  if (!chapterStateMatches || !countMatches || !indexesMatch) {
    mediaState = captured.state === "unreadable" || snapshot.media_state === "unreadable"
      ? "unreadable"
      : "mismatch";
  }

  const serverById = groupFramesByIdentity(snapshot);
  const identityPositionsMatch = captured.frames.every((local) => {
    if (!local.identityValid || local.storyboardAssetId === null) {
      return true;
    }
    const matches = serverById.get(local.storyboardAssetId) ?? [];
    return matches.length === 1 && matches[0]?.frame_index === local.frameIndex;
  });
  if (!identityPositionsMatch) {
    mediaState = "mismatch";
  }
  const activeIds = new Set<string>();
  const frameViews = captured.frames.map((local) => {
    const id = local.storyboardAssetId;
    const matches = id === null ? [] : serverById.get(id) ?? [];
    const serverFrame = matches.length === 1 ? matches[0] : undefined;
    const positionMatches = serverFrame?.frame_index === local.frameIndex;
    const identityBound = local.identityValid
      && id !== null
      && serverFrame !== undefined
      && positionMatches;
    if (id !== null && identityBound) {
      activeIds.add(id);
    }
    const verified = mediaState === "ready"
      && chapterStateMatches
      && countMatches
      && indexesMatch
      && identityPositionsMatch
      && local.identityValid
      && local.digestAvailable
      && serverFrame !== undefined
      && positionMatches
      && serverFrame.source_valid
      && serverFrame.media_revision !== null
      && Number.isSafeInteger(serverFrame.media_revision)
      && serverFrame.media_revision > 0
      && serverFrame.asset_image_digest === local.assetImageDigest
      && serverFrame.preview_digest === local.previewDigest;
    const hasSavedNote = id !== null && identityBound && snapshot.frame_notes.has(id);
    const noteProjection: PersonalProductionNoteProjection = hasSavedNote
      ? projectPersonalProductionNote(snapshot.frame_notes.get(id!))
      : {
          kind: "readable",
          value: {
            status: "unmarked",
            note: "",
            approvedMediaRevision: null,
            needsReconfirmation: false,
          },
        };
    const projected = noteStatus(noteProjection, serverFrame, verified);
    return {
      position: local.frameIndex + 1,
      status: projected.status,
      note: projected.note,
      hasSavedNote,
      verified,
    };
  });

  const orphanNotes = [...snapshot.frame_notes.entries()]
    .filter(([id]) => !activeIds.has(id))
    .map(([, value]) => ({ projection: projectPersonalProductionNote(value) }));
  const resumeId = snapshot.resume_frame_id;
  if (resumeId === null) {
    return {
      revision: snapshot.revision,
      noSavedRecord: snapshot.revision === 0 && snapshot.frame_notes.size === 0,
      mediaState,
      frames: frameViews,
      orphanNotes,
      resumePosition: null,
      resumeIsInvalid: false,
    };
  }

  const resumeMatches = captured.frames.filter((frame) => (
    frame.identityValid && frame.storyboardAssetId === resumeId
  ));
  const resumeIndex = resumeMatches[0]?.frameIndex;
  const resumeView = resumeIndex === undefined ? undefined : frameViews[resumeIndex];
  const resumePosition = resumeMatches.length === 1
    && resumeIndex !== undefined
    && resumeView?.verified === true
    ? resumeIndex + 1
    : null;
  return {
    revision: snapshot.revision,
    noSavedRecord: snapshot.revision === 0 && snapshot.frame_notes.size === 0,
    mediaState,
    frames: frameViews,
    orphanNotes,
    resumePosition,
    resumeIsInvalid: resumePosition === null,
  };
}
