// @vitest-environment node
import { describe, expect, it } from "vitest";
import { InvalidResponseError } from "./contracts";
import {
  parsePersonalProductionSnapshot,
  projectPersonalProductionNote,
} from "./personalProductionNotes";

const chapterId = "chapter-current";
const digest = "a".repeat(64);

function frame(overrides: Record<string, unknown> = {}) {
  return {
    frame_index: 0,
    storyboard_asset_id: "frame-1",
    media_revision: 2,
    source_valid: true,
    asset_image_digest: digest,
    preview_digest: null,
    invalid_reason: null,
    ...overrides,
  };
}

function snapshot(overrides: Record<string, unknown> = {}) {
  return {
    chapter_id: chapterId,
    revision: 0,
    media_state: "ready",
    frames: [frame()],
    frame_notes: {},
    resume_frame_id: null,
    ...overrides,
  };
}

describe("personal production snapshot contract", () => {
  it("preserves all wire fields and own frame-note keys without prototype lookup", () => {
    const parsed = parsePersonalProductionSnapshot(snapshot({
      revision: 4,
      frame_notes: JSON.parse('{"frame-1":{"status":"approved"},"toString":{"note":"old"}}'),
      resume_frame_id: "frame-1",
    }), chapterId);

    expect(parsed).toMatchObject({
      chapter_id: chapterId,
      revision: 4,
      media_state: "ready",
      frames: [frame()],
      resume_frame_id: "frame-1",
    });
    expect(parsed.frame_notes.get("frame-1")).toEqual({ status: "approved" });
    expect(parsed.frame_notes.get("toString")).toEqual({ note: "old" });
    expect(parsed.frame_notes.has("constructor")).toBe(false);
  });

  it("preserves revision zero and legal empty and unreadable media snapshots", () => {
    expect(parsePersonalProductionSnapshot(snapshot({
      revision: 0,
      media_state: "empty",
      frames: [],
    }), chapterId)).toMatchObject({ revision: 0, media_state: "empty", frames: [] });
    expect(parsePersonalProductionSnapshot(snapshot({
      revision: 2,
      media_state: "unreadable",
      frames: [],
    }), chapterId).media_state).toBe("unreadable");
  });

  it.each([
    ["wrong chapter", snapshot({ chapter_id: "chapter-other" })],
    ["missing revision", { ...snapshot(), revision: undefined }],
    ["negative revision", snapshot({ revision: -1 })],
    ["unsafe revision", snapshot({ revision: Number.MAX_SAFE_INTEGER + 1 })],
    ["unknown media state", snapshot({ media_state: "pending" })],
    ["non-array frames", snapshot({ frames: {} })],
    ["frames in empty state", snapshot({ media_state: "empty" })],
    ["discontinuous frame index", snapshot({ frames: [frame({ frame_index: 1 })] })],
    ["missing nullable identity", snapshot({ frames: [frame({ storyboard_asset_id: undefined })] })],
    ["invalid media version", snapshot({ frames: [frame({ media_revision: 0 })] })],
    ["invalid digest", snapshot({ frames: [frame({ asset_image_digest: "ABC" })] })],
    ["missing invalid reason", snapshot({ frames: [frame({ invalid_reason: undefined })] })],
    ["array frame notes", snapshot({ frame_notes: [] })],
    ["empty resume id", snapshot({ resume_frame_id: "" })],
  ])("rejects %s", (_label, value) => {
    expect(() => parsePersonalProductionSnapshot(value, chapterId)).toThrow(InvalidResponseError);
  });
});

describe("legacy frame note projection", () => {
  it("fills only omitted compatible fields and preserves an empty note", () => {
    expect(projectPersonalProductionNote({})).toEqual({
      kind: "readable",
      value: {
        status: "unmarked",
        note: "",
        approvedMediaRevision: null,
        needsReconfirmation: false,
      },
    });
    expect(projectPersonalProductionNote({ status: "needs_revision", note: "", approved_media_revision: null }))
      .toEqual({
        kind: "readable",
        value: {
          status: "needs_revision",
          note: "",
          approvedMediaRevision: null,
          needsReconfirmation: false,
        },
      });
  });

  it.each([
    null,
    [],
    { status: "unknown" },
    { note: 42 },
    { approved_media_revision: 0 },
    { needs_reconfirmation: "false" },
  ])("isolates an invalid legacy entry (%j)", (value) => {
    expect(projectPersonalProductionNote(value)).toEqual({ kind: "unreadable" });
  });
});
