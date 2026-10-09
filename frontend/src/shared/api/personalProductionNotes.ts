import { InvalidResponseError } from "./contracts";

export type PersonalProductionMediaState = "ready" | "empty" | "unreadable";
export type PersonalProductionNoteStatus = "unmarked" | "needs_revision" | "approved";

export interface PersonalProductionFrameSnapshot {
  frame_index: number;
  storyboard_asset_id: string | null;
  media_revision: number | null;
  source_valid: boolean;
  asset_image_digest: string | null;
  preview_digest: string | null;
  invalid_reason: string | null;
}

export interface PersonalProductionSnapshot {
  chapter_id: string;
  revision: number;
  media_state: PersonalProductionMediaState;
  frames: PersonalProductionFrameSnapshot[];
  frame_notes: ReadonlyMap<string, unknown>;
  resume_frame_id: string | null;
}

export interface ProjectedPersonalProductionNote {
  status: PersonalProductionNoteStatus;
  note: string;
  approvedMediaRevision: number | null;
  needsReconfirmation: boolean;
}

export type PersonalProductionNoteProjection =
  | { kind: "readable"; value: ProjectedPersonalProductionNote }
  | { kind: "unreadable" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredNonEmptyString(value: unknown, field: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new InvalidResponseError("制作记录中的 " + field + " 字段格式无效。");
  }
  return value;
}

function requiredStringOrNull(value: unknown, field: string): string | null {
  if (value !== null && typeof value !== "string") {
    throw new InvalidResponseError("制作记录中的 " + field + " 字段格式无效。");
  }
  return value;
}

function requiredSafeInteger(value: unknown, field: string, minimum: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum) {
    throw new InvalidResponseError("制作记录中的 " + field + " 字段格式无效。");
  }
  return value;
}

function requiredDigest(value: unknown, field: string): string | null {
  if (value === null) {
    return null;
  }
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) {
    throw new InvalidResponseError("制作记录中的 " + field + " 字段格式无效。");
  }
  return value;
}

function parseFrame(value: unknown, expectedIndex: number): PersonalProductionFrameSnapshot {
  if (!isRecord(value)) {
    throw new InvalidResponseError("制作记录中的分镜快照格式无效。");
  }
  const frameIndex = requiredSafeInteger(value.frame_index, "frame_index", 0);
  if (frameIndex !== expectedIndex) {
    throw new InvalidResponseError("制作记录中的分镜位置不连续。");
  }

  const mediaRevision = value.media_revision;
  const parsedMediaRevision = mediaRevision === null
    ? null
    : requiredSafeInteger(mediaRevision, "media_revision", 1);
  if (typeof value.source_valid !== "boolean") {
    throw new InvalidResponseError("制作记录中的 source_valid 字段格式无效。");
  }

  return {
    frame_index: frameIndex,
    storyboard_asset_id: requiredStringOrNull(value.storyboard_asset_id, "storyboard_asset_id"),
    media_revision: parsedMediaRevision,
    source_valid: value.source_valid,
    asset_image_digest: requiredDigest(value.asset_image_digest, "asset_image_digest"),
    preview_digest: requiredDigest(value.preview_digest, "preview_digest"),
    invalid_reason: requiredStringOrNull(value.invalid_reason, "invalid_reason"),
  };
}

function parseFrameNotes(value: unknown): ReadonlyMap<string, unknown> {
  if (!isRecord(value)) {
    throw new InvalidResponseError("制作记录中的 frame_notes 字段格式无效。");
  }
  const entries = new Map<string, unknown>();
  for (const key of Object.keys(value)) {
    if (Object.prototype.hasOwnProperty.call(value, key)) {
      entries.set(key, value[key]);
    }
  }
  return entries;
}

export function parsePersonalProductionSnapshot(
  value: unknown,
  expectedChapterId: string,
): PersonalProductionSnapshot {
  if (!isRecord(value)) {
    throw new InvalidResponseError("服务器返回的制作记录格式无效。");
  }

  const chapterId = requiredNonEmptyString(value.chapter_id, "chapter_id");
  if (chapterId !== expectedChapterId) {
    throw new InvalidResponseError("服务器返回了不属于当前章节的制作记录。");
  }
  const revision = requiredSafeInteger(value.revision, "revision", 0);
  const mediaState = value.media_state;
  if (mediaState !== "ready" && mediaState !== "empty" && mediaState !== "unreadable") {
    throw new InvalidResponseError("制作记录中的 media_state 字段格式无效。");
  }
  if (!Array.isArray(value.frames)) {
    throw new InvalidResponseError("制作记录中的 frames 字段格式无效。");
  }
  const frames = value.frames.map((frame, index) => parseFrame(frame, index));
  if (mediaState !== "ready" && frames.length !== 0) {
    throw new InvalidResponseError("不可读或空媒体状态不能包含分镜快照。");
  }

  const resumeFrameId = requiredStringOrNull(value.resume_frame_id, "resume_frame_id");
  if (resumeFrameId !== null && resumeFrameId.length === 0) {
    throw new InvalidResponseError("制作记录中的 resume_frame_id 字段格式无效。");
  }

  return {
    chapter_id: chapterId,
    revision,
    media_state: mediaState,
    frames,
    frame_notes: parseFrameNotes(value.frame_notes),
    resume_frame_id: resumeFrameId,
  };
}

const noteStatuses = new Set<PersonalProductionNoteStatus>([
  "unmarked",
  "needs_revision",
  "approved",
]);

export function projectPersonalProductionNote(value: unknown): PersonalProductionNoteProjection {
  if (!isRecord(value)) {
    return { kind: "unreadable" };
  }

  const status = value.status === undefined ? "unmarked" : value.status;
  const note = value.note === undefined ? "" : value.note;
  const approvedRevision = value.approved_media_revision === undefined
    ? null
    : value.approved_media_revision;
  const needsReconfirmation = value.needs_reconfirmation === undefined
    ? false
    : value.needs_reconfirmation;
  if (
    typeof status !== "string"
    || !noteStatuses.has(status as PersonalProductionNoteStatus)
    || typeof note !== "string"
    || (approvedRevision !== null
      && (typeof approvedRevision !== "number" || !Number.isSafeInteger(approvedRevision) || approvedRevision < 1))
    || typeof needsReconfirmation !== "boolean"
  ) {
    return { kind: "unreadable" };
  }

  return {
    kind: "readable",
    value: {
      status: status as PersonalProductionNoteStatus,
      note,
      approvedMediaRevision: approvedRevision,
      needsReconfirmation,
    },
  };
}


export interface PersonalProductionNoteFrameUpdate {
  readonly storyboard_asset_id: string;
  readonly expected_media_revision: number;
  readonly status: PersonalProductionNoteStatus;
  readonly note: string;
}

export interface PersonalProductionNoteUpdate {
  readonly expected_revision: number;
  readonly frames: readonly [PersonalProductionNoteFrameUpdate];
}

function hasExactOwnKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Reflect.ownKeys(value);
  return actual.length === expected.length
    && expected.every((key) => Object.prototype.hasOwnProperty.call(value, key))
    && actual.every((key) => typeof key === "string" && expected.includes(key));
}

function codePointLength(value: string): number {
  return Array.from(value).length;
}

/** Validates and copies the strict one-frame request before it reaches an HTTP adapter. */
export function parsePersonalProductionNoteUpdate(value: unknown): PersonalProductionNoteUpdate {
  if (
    typeof value !== "object"
    || value === null
    || Array.isArray(value)
    || !hasExactOwnKeys(value as Record<string, unknown>, ["expected_revision", "frames"])
  ) {
    throw new InvalidResponseError("制作记录更新格式无效。");
  }

  const record = value as Record<string, unknown>;
  const expectedRevision = record.expected_revision;
  const framesValue = record.frames;
  if (
    typeof expectedRevision !== "number"
    || !Number.isSafeInteger(expectedRevision)
    || expectedRevision < 0
    || !Array.isArray(framesValue)
    || framesValue.length !== 1
    || Object.keys(framesValue).length !== 1
    || !Object.prototype.hasOwnProperty.call(framesValue, 0)
  ) {
    throw new InvalidResponseError("制作记录更新版本或镜头数量无效。");
  }

  const frameValue: unknown = framesValue[0];
  if (
    typeof frameValue !== "object"
    || frameValue === null
    || Array.isArray(frameValue)
    || !hasExactOwnKeys(frameValue as Record<string, unknown>, [
      "storyboard_asset_id",
      "expected_media_revision",
      "status",
      "note",
    ])
  ) {
    throw new InvalidResponseError("制作记录更新镜头格式无效。");
  }

  const frame = frameValue as Record<string, unknown>;
  const storyboardAssetId = frame.storyboard_asset_id;
  const expectedMediaRevision = frame.expected_media_revision;
  const status = frame.status;
  const note = frame.note;
  if (
    typeof storyboardAssetId !== "string"
    || storyboardAssetId.length === 0
    || codePointLength(storyboardAssetId) > 36
    || typeof expectedMediaRevision !== "number"
    || !Number.isSafeInteger(expectedMediaRevision)
    || expectedMediaRevision < 1
    || typeof status !== "string"
    || !noteStatuses.has(status as PersonalProductionNoteStatus)
    || typeof note !== "string"
    || codePointLength(note) > 2_000
  ) {
    throw new InvalidResponseError("制作记录更新字段无效。");
  }

  return {
    expected_revision: expectedRevision,
    frames: [{
      storyboard_asset_id: storyboardAssetId,
      expected_media_revision: expectedMediaRevision,
      status: status as PersonalProductionNoteStatus,
      note,
    }],
  };
}

export interface PersonalProductionResumeUpdate {
  readonly expected_revision: number;
  readonly resume_frame_id: string | null;
}

function hasExactResumeKeys(value: Record<string, unknown>): boolean {
  const keys = Reflect.ownKeys(value);
  return keys.length === 2
    && Object.prototype.hasOwnProperty.call(value, "expected_revision")
    && Object.prototype.hasOwnProperty.call(value, "resume_frame_id")
    && keys.every((key) => key === "expected_revision" || key === "resume_frame_id");
}

/** Validates the independent two-field resume update without widening the note DTO. */
export function parsePersonalProductionResumeUpdate(value: unknown): PersonalProductionResumeUpdate {
  if (
    typeof value !== "object"
    || value === null
    || Array.isArray(value)
    || !hasExactResumeKeys(value as Record<string, unknown>)
  ) {
    throw new InvalidResponseError("续作位置更新格式无效。");
  }

  const record = value as Record<string, unknown>;
  const expectedRevision = record.expected_revision;
  const resumeFrameId = record.resume_frame_id;
  if (
    typeof expectedRevision !== "number"
    || !Number.isSafeInteger(expectedRevision)
    || expectedRevision < 0
    || expectedRevision === Number.MAX_SAFE_INTEGER
    || (resumeFrameId !== null
      && (
        typeof resumeFrameId !== "string"
        || resumeFrameId.trim() === ""
        || Array.from(resumeFrameId).length > 36
      ))
  ) {
    throw new InvalidResponseError("续作位置更新字段无效。");
  }

  return {
    expected_revision: expectedRevision,
    resume_frame_id: resumeFrameId as string | null,
  };
}
