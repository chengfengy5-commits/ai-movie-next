import { InvalidResponseError, type Chapter, type StoryboardAsset, type StoryboardFrame } from "../../../shared/api/contracts";
import {
  parsePersonalProductionNoteUpdate,
  parsePersonalProductionSnapshot,
  projectPersonalProductionNote,
  type PersonalProductionNoteStatus,
  type PersonalProductionNoteUpdate,
  type PersonalProductionSnapshot,
} from "../../../shared/api/personalProductionNotes";
import type {
  CapturedPersonalProductionFrame,
  CapturedPersonalProductionMedia,
  PersonalProductionFrameView,
  PersonalProductionReadout,
} from "./projection";

export interface PersonalProductionNoteEditCandidate {
  readonly chapterId: string;
  readonly frameIndex: number;
  readonly position: number;
  readonly storyboardAssetId: string;
  readonly expectedRevision: number;
  readonly expectedMediaRevision: number;
  readonly note: string;
  readonly initialStatus: PersonalProductionNoteStatus | null;
  readonly approvalEligible: boolean;
  readonly row: PersonalProductionFrameView;
  readonly frame: StoryboardFrame;
  readonly capturedFrame: CapturedPersonalProductionFrame;
  readonly snapshot: PersonalProductionSnapshot;
  readonly captured: CapturedPersonalProductionMedia;
  readonly readout: PersonalProductionReadout;
}

export interface ResolvePersonalProductionNoteEditInput {
  chapter: Chapter;
  assets: readonly StoryboardAsset[];
  captured: CapturedPersonalProductionMedia;
  snapshot: PersonalProductionSnapshot;
  readout: PersonalProductionReadout;
  row: PersonalProductionFrameView;
  mediaSnapshotAvailable: boolean;
}

function firstStoryboardIdentity(frame: StoryboardFrame): string | null {
  if (!Array.isArray(frame.storyboard)) {
    return null;
  }
  const identity: unknown = frame.storyboard[0];
  return typeof identity === "string" && identity.trim() !== "" ? identity : null;
}

function countRawAssetId(assets: readonly StoryboardAsset[], id: string): number {
  return assets.reduce((count, asset) => count + (asset.id === id ? 1 : 0), 0);
}

function countRawStoryboardId(chapter: Chapter, id: string): number {
  if (chapter.content === null) {
    return 0;
  }
  return chapter.content.reduce((count, frame) => count + (firstStoryboardIdentity(frame) === id ? 1 : 0), 0);
}

function hasMatchingChapterStructure(
  chapter: Chapter,
  captured: CapturedPersonalProductionMedia,
  snapshot: PersonalProductionSnapshot,
): boolean {
  if (
    chapter.content === null
    || captured.state !== "ready"
    || snapshot.media_state !== "ready"
    || snapshot.chapter_id !== chapter.id
    || snapshot.frames.length !== chapter.content.length
    || captured.frames.length !== chapter.content.length
  ) {
    return false;
  }

  return snapshot.frames.every((frame, index) => {
    const local = captured.frames[index];
    const source = chapter.content?.[index];
    if (local === undefined || source === undefined || frame.frame_index !== index) {
      return false;
    }
    const rawId = firstStoryboardIdentity(source);
    return rawId === null || !local.identityValid
      ? true
      : frame.storyboard_asset_id === rawId && local.storyboardAssetId === rawId;
  });
}

function canApprove(
  row: PersonalProductionFrameView,
  serverFrame: PersonalProductionSnapshot["frames"][number],
  capturedFrame: CapturedPersonalProductionFrame,
): boolean {
  if (
    !row.verified
    || !capturedFrame.digestAvailable
    || !serverFrame.source_valid
    || serverFrame.media_revision === null
    || !Number.isSafeInteger(serverFrame.media_revision)
    || serverFrame.media_revision < 1
    || (serverFrame.asset_image_digest === null && serverFrame.preview_digest === null)
    || serverFrame.asset_image_digest !== capturedFrame.assetImageDigest
    || serverFrame.preview_digest !== capturedFrame.previewDigest
  ) {
    return false;
  }
  return true;
}

/** Resolves only a current row whose original frame and server identity agree. */
export function resolvePersonalProductionNoteEditCandidate(
  input: ResolvePersonalProductionNoteEditInput,
): PersonalProductionNoteEditCandidate | null {
  const { chapter, assets, captured, snapshot, readout, row } = input;
  if (
    !input.mediaSnapshotAvailable
    || readout.mediaState !== "ready"
    || readout.revision !== snapshot.revision
    || !hasMatchingChapterStructure(chapter, captured, snapshot)
    || !Number.isSafeInteger(row.position)
    || row.position < 1
    || readout.frames.filter((candidate) => candidate === row).length !== 1
    || readout.frames.filter((candidate) => candidate.position === row.position).length !== 1
    || row.status === "unreadable"
  ) {
    return null;
  }

  const frameIndex = row.position - 1;
  const frame = chapter.content?.[frameIndex];
  const capturedFrame = captured.frames[frameIndex];
  const rawId = frame === undefined ? null : firstStoryboardIdentity(frame);
  if (
    frame === undefined
    || capturedFrame === undefined
    || rawId === null
    || countRawStoryboardId(chapter, rawId) !== 1
    || countRawAssetId(assets, rawId) !== 1
    || !capturedFrame.identityValid
    || capturedFrame.candidateId !== rawId
    || capturedFrame.storyboardAssetId !== rawId
  ) {
    return null;
  }
  const matchingAssets = assets.filter((asset) => asset.id === rawId);
  if (
    matchingAssets.length !== 1
    || matchingAssets[0]?.chapter_id !== chapter.id
    || matchingAssets[0]?.series_id !== chapter.series_id
  ) {
    return null;
  }

  const matchingServerFrames = snapshot.frames.filter((serverFrame) => (
    serverFrame.storyboard_asset_id === rawId
  ));
  const serverFrame = matchingServerFrames.length === 1 ? matchingServerFrames[0] : undefined;
  if (
    serverFrame === undefined
    || serverFrame.frame_index !== frameIndex
    || serverFrame.source_valid !== true
    || serverFrame.media_revision === null
    || !Number.isSafeInteger(serverFrame.media_revision)
    || serverFrame.media_revision < 1
  ) {
    return null;
  }

  const hasSavedNote = snapshot.frame_notes.has(rawId);
  if (row.hasSavedNote !== hasSavedNote) {
    return null;
  }
  const currentNote = hasSavedNote
    ? projectPersonalProductionNote(snapshot.frame_notes.get(rawId))
    : { kind: "readable" as const, value: {
        status: "unmarked" as const,
        note: "",
        approvedMediaRevision: null,
        needsReconfirmation: false,
      } };
  if (currentNote.kind === "unreadable") {
    return null;
  }

  const approvalEligible = canApprove(row, serverFrame, capturedFrame);
  let initialStatus: PersonalProductionNoteStatus | null;
  if (!hasSavedNote) {
    initialStatus = "unmarked";
  } else if (currentNote.value.status === "approved") {
    const currentApprovalIsValid = approvalEligible
      && !currentNote.value.needsReconfirmation
      && currentNote.value.approvedMediaRevision === serverFrame.media_revision;
    initialStatus = currentApprovalIsValid ? "approved" : null;
  } else if (currentNote.value.needsReconfirmation) {
    initialStatus = null;
  } else {
    initialStatus = currentNote.value.status;
  }

  return {
    chapterId: chapter.id,
    frameIndex,
    position: row.position,
    storyboardAssetId: rawId,
    expectedRevision: snapshot.revision,
    expectedMediaRevision: serverFrame.media_revision,
    note: currentNote.value.note,
    initialStatus,
    approvalEligible,
    row,
    frame,
    capturedFrame,
    snapshot,
    captured,
    readout,
  };
}

export function createPersonalProductionNoteUpdate(
  candidate: PersonalProductionNoteEditCandidate,
  status: PersonalProductionNoteStatus,
  note: string,
): PersonalProductionNoteUpdate {
  if (status === "approved" && !candidate.approvalEligible) {
    throw new InvalidResponseError("当前镜头尚未通过媒体核对，不能保存为认可状态。");
  }
  return parsePersonalProductionNoteUpdate({
    expected_revision: candidate.expectedRevision,
    frames: [{
      storyboard_asset_id: candidate.storyboardAssetId,
      expected_media_revision: candidate.expectedMediaRevision,
      status,
      note,
    }],
  });
}

function sameFrameStructure(
  previous: PersonalProductionSnapshot["frames"][number],
  next: PersonalProductionSnapshot["frames"][number],
): boolean {
  return previous.frame_index === next.frame_index
    && previous.storyboard_asset_id === next.storyboard_asset_id;
}

export interface ValidatePersonalProductionNoteSaveInput {
  candidate: PersonalProductionNoteEditCandidate;
  update: PersonalProductionNoteUpdate;
  value: unknown;
}

/** Accepts a PUT response only when it represents the exact next one-frame snapshot. */
export function validatePersonalProductionNoteSaveResponse(
  input: ValidatePersonalProductionNoteSaveInput,
): PersonalProductionSnapshot {
  const { candidate, update } = input;
  const responseValue = input.value !== null
    && typeof input.value === "object"
    && !Array.isArray(input.value)
    && "frame_notes" in input.value
    && input.value.frame_notes instanceof Map
    ? {
        ...input.value,
        frame_notes: Object.fromEntries(input.value.frame_notes),
      }
    : input.value;
  const response = parsePersonalProductionSnapshot(responseValue, candidate.chapterId);
  const oldSnapshot = candidate.snapshot;
  if (
    response.revision !== oldSnapshot.revision + 1
    || response.media_state !== oldSnapshot.media_state
    || response.frames.length !== oldSnapshot.frames.length
    || response.frames.some((frame, index) => {
      const oldFrame = oldSnapshot.frames[index];
      return oldFrame === undefined || frame.frame_index !== index || !sameFrameStructure(oldFrame, frame);
    })
  ) {
    throw new InvalidResponseError("服务器返回的制作记录不是本次保存的下一版本。");
  }

  const updatedFrame = response.frames[candidate.frameIndex];
  const oldFrame = oldSnapshot.frames[candidate.frameIndex];
  if (
    updatedFrame === undefined
    || oldFrame === undefined
    || updatedFrame.storyboard_asset_id !== candidate.storyboardAssetId
    || updatedFrame.frame_index !== candidate.frameIndex
    || updatedFrame.media_revision !== candidate.expectedMediaRevision
    || !updatedFrame.source_valid
    || oldFrame.storyboard_asset_id !== candidate.storyboardAssetId
    || oldFrame.frame_index !== candidate.frameIndex
    || oldFrame.media_revision !== candidate.expectedMediaRevision
    || update.expected_revision !== oldSnapshot.revision
    || update.frames.length !== 1
    || update.frames[0]?.storyboard_asset_id !== candidate.storyboardAssetId
    || update.frames[0]?.expected_media_revision !== candidate.expectedMediaRevision
  ) {
    throw new InvalidResponseError("服务器返回的制作记录与当前镜头版本不一致。");
  }

  const savedNote = projectPersonalProductionNote(response.frame_notes.get(candidate.storyboardAssetId));
  if (
    savedNote.kind !== "readable"
    || savedNote.value.status !== update.frames[0].status
    || savedNote.value.note !== update.frames[0].note
  ) {
    throw new InvalidResponseError("服务器返回的镜头记录与本次保存内容不一致。");
  }

  if (update.frames[0].status === "approved") {
    const beforeFrame = oldFrame;
    const capturedFrame = candidate.capturedFrame;
    if (
      savedNote.value.needsReconfirmation
      || savedNote.value.approvedMediaRevision !== candidate.expectedMediaRevision
      || (updatedFrame.asset_image_digest === null && updatedFrame.preview_digest === null)
      || updatedFrame.asset_image_digest !== beforeFrame.asset_image_digest
      || updatedFrame.preview_digest !== beforeFrame.preview_digest
      || updatedFrame.asset_image_digest !== capturedFrame.assetImageDigest
      || updatedFrame.preview_digest !== capturedFrame.previewDigest
      || !capturedFrame.digestAvailable
      || !candidate.row.verified
    ) {
      throw new InvalidResponseError("服务器返回的认可记录无法与保存前媒体核对结果一致。");
    }
  } else if (
    savedNote.value.approvedMediaRevision !== null
    || savedNote.value.needsReconfirmation
  ) {
    throw new InvalidResponseError("服务器返回的非认可记录仍带有旧认可信息。");
  }

  return response;
}
