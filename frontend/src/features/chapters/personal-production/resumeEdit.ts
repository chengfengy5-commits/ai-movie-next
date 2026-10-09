import { InvalidResponseError, type Chapter, type StoryboardAsset, type StoryboardFrame } from "../../../shared/api/contracts";
import {
  parsePersonalProductionResumeUpdate,
  parsePersonalProductionSnapshot,
  type PersonalProductionResumeUpdate,
  type PersonalProductionSnapshot,
} from "../../../shared/api/personalProductionNotes";
import type {
  CapturedPersonalProductionFrame,
  CapturedPersonalProductionMedia,
  PersonalProductionFrameView,
  PersonalProductionReadout,
} from "./projection";

export interface PersonalProductionResumeCandidate {
  readonly chapterId: string;
  readonly seriesId: string;
  readonly frameIndex: number;
  readonly position: number;
  readonly storyboardAssetId: string;
  readonly row: PersonalProductionFrameView;
  readonly frame: StoryboardFrame;
  readonly capturedFrame: CapturedPersonalProductionFrame;
  readonly serverFrame: PersonalProductionSnapshot["frames"][number];
  readonly snapshot: PersonalProductionSnapshot;
  readonly captured: CapturedPersonalProductionMedia;
  readonly readout: PersonalProductionReadout;
}

export interface ResolvePersonalProductionResumeCandidatesInput {
  readonly chapter: Chapter;
  readonly seriesId: string;
  readonly assets: readonly StoryboardAsset[];
  readonly captured: CapturedPersonalProductionMedia;
  readonly snapshot: PersonalProductionSnapshot;
  readonly readout: PersonalProductionReadout;
  readonly mediaSnapshotAvailable: boolean;
}

function firstStoryboardIdentity(frame: StoryboardFrame): string | null {
  if (!Array.isArray(frame.storyboard)) {
    return null;
  }
  const identity: unknown = frame.storyboard[0];
  return typeof identity === "string" && identity.trim() !== "" ? identity : null;
}

function countChapterIdentity(chapter: Chapter, identity: string): number {
  if (!Array.isArray(chapter.content)) {
    return 0;
  }
  return chapter.content.reduce(
    (count, frame) => count + (firstStoryboardIdentity(frame) === identity ? 1 : 0),
    0,
  );
}

function countAssetIdentity(assets: readonly StoryboardAsset[], identity: string): number {
  return assets.reduce((count, asset) => count + (asset.id === identity ? 1 : 0), 0);
}

function countSnapshotIdentity(snapshot: PersonalProductionSnapshot, identity: string): number {
  return snapshot.frames.reduce(
    (count, frame) => count + (frame.storyboard_asset_id === identity ? 1 : 0),
    0,
  );
}

function hasMatchingSnapshotStructure(
  input: ResolvePersonalProductionResumeCandidatesInput,
): boolean {
  const { chapter, captured, snapshot, readout } = input;
  const sourceFrames = chapter.content;
  if (
    sourceFrames === null
    || chapter.series_id !== input.seriesId
    || captured.state !== "ready"
    || snapshot.chapter_id !== chapter.id
    || snapshot.media_state !== "ready"
    || readout.mediaState !== "ready"
    || readout.revision !== snapshot.revision
    || sourceFrames.length !== captured.frames.length
    || sourceFrames.length !== snapshot.frames.length
    || sourceFrames.length !== readout.frames.length
  ) {
    return false;
  }

  return sourceFrames.every((source, index) => {
    const capturedFrame = captured.frames[index];
    const serverFrame = snapshot.frames[index];
    const row = readout.frames[index];
    if (
      capturedFrame === undefined
      || serverFrame === undefined
      || row === undefined
      || capturedFrame.frameIndex !== index
      || serverFrame.frame_index !== index
      || row.position !== index + 1
    ) {
      return false;
    }

    const sourceIdentity = firstStoryboardIdentity(source);
    if (capturedFrame.candidateId !== sourceIdentity) {
      return false;
    }

    if (capturedFrame.identityValid) {
      return sourceIdentity !== null
        && capturedFrame.storyboardAssetId === sourceIdentity
        && serverFrame.storyboard_asset_id === sourceIdentity;
    }

    return capturedFrame.storyboardAssetId === null
      && serverFrame.storyboard_asset_id === null;
  });
}

/** Resolves writable resume choices through one matching chapter/media/read snapshot. */
export function resolvePersonalProductionResumeCandidates(
  input: ResolvePersonalProductionResumeCandidatesInput,
): PersonalProductionResumeCandidate[] {
  if (!input.mediaSnapshotAvailable || !hasMatchingSnapshotStructure(input)) {
    return [];
  }

  const chapter = input.chapter;
  const sourceFrames = chapter.content;
  if (sourceFrames === null) {
    return [];
  }

  const candidates: PersonalProductionResumeCandidate[] = [];
  for (let index = 0; index < sourceFrames.length; index += 1) {
    const frame = sourceFrames[index];
    const row = input.readout.frames[index];
    const capturedFrame = input.captured.frames[index];
    const serverFrame = input.snapshot.frames[index];
    const identity = frame === undefined ? null : firstStoryboardIdentity(frame);
    if (
      frame === undefined
      || row === undefined
      || capturedFrame === undefined
      || serverFrame === undefined
      || identity === null
      || Array.from(identity).length > 36
      || countChapterIdentity(chapter, identity) !== 1
      || countAssetIdentity(input.assets, identity) !== 1
      || countSnapshotIdentity(input.snapshot, identity) !== 1
      || capturedFrame.frameIndex !== index
      || !capturedFrame.identityValid
      || capturedFrame.candidateId !== identity
      || capturedFrame.storyboardAssetId !== identity
      || serverFrame.frame_index !== index
      || serverFrame.storyboard_asset_id !== identity
      || serverFrame.source_valid !== true
      || row.position !== index + 1
      || input.readout.frames.filter((candidate) => candidate === row).length !== 1
      || input.readout.frames.filter((candidate) => candidate.position === row.position).length !== 1
    ) {
      continue;
    }

    const asset = input.assets.find((candidate) => candidate.id === identity);
    if (
      asset === undefined
      || asset.chapter_id !== chapter.id
      || asset.series_id !== input.seriesId
    ) {
      continue;
    }

    candidates.push({
      chapterId: chapter.id,
      seriesId: input.seriesId,
      frameIndex: index,
      position: index + 1,
      storyboardAssetId: identity,
      row,
      frame,
      capturedFrame,
      serverFrame,
      snapshot: input.snapshot,
      captured: input.captured,
      readout: input.readout,
    });
  }

  return candidates;
}

/** A ready owner may clear a saved value even when its old target no longer maps to media. */
export function canClearPersonalProductionResume(
  chapterId: string,
  snapshot: PersonalProductionSnapshot,
): boolean {
  return snapshot.chapter_id === chapterId
    && Number.isSafeInteger(snapshot.revision)
    && snapshot.revision >= 0
    && snapshot.revision < Number.MAX_SAFE_INTEGER
    && snapshot.resume_frame_id !== null;
}

export function createPersonalProductionResumeUpdate(
  snapshot: PersonalProductionSnapshot,
  candidate: PersonalProductionResumeCandidate | null,
): PersonalProductionResumeUpdate {
  if (
    !Number.isSafeInteger(snapshot.revision)
    || snapshot.revision < 0
    || snapshot.revision >= Number.MAX_SAFE_INTEGER
  ) {
    throw new InvalidResponseError("当前记录版本不能安全更新续作位置。");
  }
  if (candidate !== null && (
    candidate.snapshot !== snapshot
    || candidate.chapterId !== snapshot.chapter_id
    || candidate.storyboardAssetId.trim() === ""
    || candidate.frameIndex !== candidate.position - 1
    || candidate.serverFrame.frame_index !== candidate.frameIndex
    || candidate.serverFrame.storyboard_asset_id !== candidate.storyboardAssetId
    || candidate.serverFrame.source_valid !== true
  )) {
    throw new InvalidResponseError("续作位置与当前读取快照不一致。");
  }

  return parsePersonalProductionResumeUpdate({
    expected_revision: snapshot.revision,
    resume_frame_id: candidate?.storyboardAssetId ?? null,
  });
}

function snapshotValueForParser(value: unknown): unknown {
  if (
    value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && "frame_notes" in value
    && value.frame_notes instanceof Map
  ) {
    return {
      ...value,
      frame_notes: Object.fromEntries(value.frame_notes),
    };
  }
  return value;
}

export interface ValidatePersonalProductionResumeSaveInput {
  readonly chapterId: string;
  readonly update: PersonalProductionResumeUpdate;
  readonly candidate: PersonalProductionResumeCandidate | null;
  readonly value: unknown;
}

/** Validates the requested next revision and adopts the server's complete snapshot. */
export function validatePersonalProductionResumeSaveResponse(
  input: ValidatePersonalProductionResumeSaveInput,
): PersonalProductionSnapshot {
  const update = parsePersonalProductionResumeUpdate(input.update);
  const response = parsePersonalProductionSnapshot(
    snapshotValueForParser(input.value),
    input.chapterId,
  );
  if (
    response.revision !== update.expected_revision + 1
    || response.resume_frame_id !== update.resume_frame_id
  ) {
    throw new InvalidResponseError("服务器返回的续作位置不是本次请求的下一版本。");
  }

  if (update.resume_frame_id === null) {
    if (input.candidate !== null) {
      throw new InvalidResponseError("清除续作位置不能携带设置目标。");
    }
    return response;
  }

  const candidate = input.candidate;
  if (
    candidate === null
    || candidate.chapterId !== input.chapterId
    || candidate.snapshot.revision !== update.expected_revision
    || candidate.storyboardAssetId !== update.resume_frame_id
  ) {
    throw new InvalidResponseError("本次续作位置设置目标已失效。");
  }

  const matchingFrames = response.frames.filter(
    (frame) => frame.storyboard_asset_id === update.resume_frame_id,
  );
  const target = matchingFrames.length === 1 ? matchingFrames[0] : undefined;
  if (
    target === undefined
    || target.frame_index !== candidate.frameIndex
    || target.source_valid !== true
  ) {
    throw new InvalidResponseError("服务器返回的续作目标无法与本次镜头身份核对。");
  }

  return response;
}
