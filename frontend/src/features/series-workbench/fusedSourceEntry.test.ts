import { describe, expect, it } from "vitest";
import type { Character, Prop, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import { createFusedFrameSource } from "./fusedSourceEntry";

const character: Character = {
  id: "character-a",
  series_id: "series-a",
  name: "角色甲",
  aliases: null,
  canonical_key: null,
  description: null,
  image_url: "/character.png",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  gender: null,
  age: null,
  role: null,
  appearance: null,
  audio_url: null,
  voice_ref: null,
};

const prop: Prop = {
  id: "prop-a",
  series_id: "series-a",
  name: "道具甲",
  aliases: null,
  canonical_key: null,
  description: null,
  image_url: "/prop.png",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const storyboardAsset = (frameIndex: number): StoryboardAsset => ({
  id: "storyboard-a",
  series_id: "series-a",
  chapter_id: "chapter-a",
  frame_index: frameIndex,
  name: "镜头甲",
  description: null,
  image_url: "/frame.png",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
});

function createSource(
  frame: StoryboardFrame,
  chapterContent: readonly StoryboardFrame[] = [frame],
  storyboardAssets: readonly StoryboardAsset[] = [storyboardAsset(0)],
) {
  return createFusedFrameSource({
    seriesId: "series-a",
    chapterId: "chapter-a",
    frameIndex: 0,
    chapterContent,
    storyboardAssets,
    memberType: "character",
    memberDirectory: [character],
  });
}

describe("frame fused-source proof", () => {
  it("captures the real storyboard identity and all unique referenced character IDs", () => {
    const frame: StoryboardFrame = { storyboard: ["storyboard-a"], character: ["character-a"], text: "镜头正文" };
    const result = createSource(frame);

    expect(result).toMatchObject({
      status: "ready",
      memberType: "character",
      memberAssetIds: ["character-a"],
      target: {
        chapterId: "chapter-a",
        frameIndex: 0,
        storyboardAssetId: "storyboard-a",
        expectedFrame: frame,
      },
    });
  });

  it("uses the original position and complete chapter snapshot when no stable storyboard ID exists", () => {
    const frame: StoryboardFrame = { character: ["character-a"], text: "无编号镜头" };
    const result = createSource(frame, [frame, { text: "后一镜" }], []);

    expect(result).toMatchObject({
      status: "ready",
      target: {
        chapterId: "chapter-a",
        frameIndex: 0,
        expectedFrame: frame,
        expectedContent: [frame, { text: "后一镜" }],
      },
    });
    if (result.status === "ready") {
      expect(Object.isFrozen(result.target.expectedFrame)).toBe(true);
      expect(Object.isFrozen(result.target.expectedContent)).toBe(true);
    }
  });

  it("rejects duplicate frame identity, mismatched directory ownership, and incomplete member identity", () => {
    const frame: StoryboardFrame = { storyboard: ["storyboard-a"], character: ["character-a"] };
    expect(createSource(frame, [frame, frame], [storyboardAsset(0)])).toMatchObject({ status: "unavailable" });
    expect(createSource(frame, [frame], [storyboardAsset(1)])).toMatchObject({ status: "unavailable" });
    expect(createSource({ storyboard: ["storyboard-a"], character: ["missing"] })).toMatchObject({ status: "unavailable" });
  });

  it("rejects indistinguishable no-ID frames instead of guessing their position", () => {
    const first: StoryboardFrame = { character: ["character-a"], text: "相同内容" };
    const second: StoryboardFrame = { character: ["character-a"], text: "相同内容" };
    expect(createSource(first, [first, second], [])).toMatchObject({ status: "unavailable" });
  });

  it("captures props independently from the character directory", () => {
    const result = createFusedFrameSource({
      seriesId: "series-a",
      chapterId: "chapter-a",
      frameIndex: 0,
      chapterContent: [{ storyboard: ["storyboard-a"], prop: ["prop-a"] }],
      storyboardAssets: [storyboardAsset(0)],
      memberType: "prop",
      memberDirectory: [prop],
    });

    expect(result).toMatchObject({ status: "ready", memberType: "prop", memberAssetIds: ["prop-a"] });
  });

  it("allows a valid target with no linked members so the user can choose them afterward", () => {
    const frame: StoryboardFrame = { storyboard: ["storyboard-a"], character: [] };
    expect(createSource(frame)).toMatchObject({
      status: "ready",
      memberType: "character",
      memberAssetIds: [],
      target: { chapterId: "chapter-a", storyboardAssetId: "storyboard-a" },
    });
  });

  it("does not treat a null storyboard field as a missing stable identity", () => {
    const frame = { storyboard: null, character: ["character-a"] } as unknown as StoryboardFrame;
    expect(createSource(frame)).toMatchObject({ status: "unavailable" });
  });

  it.each([Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, undefined])(
    "rejects lossy frame data instead of normalizing it during JSON copying (%s)",
    (invalidValue: unknown) => {
      const frame = {
        storyboard: ["storyboard-a"],
        character: ["character-a"],
        malformedMetadata: invalidValue,
      } as unknown as StoryboardFrame;
      expect(createSource(frame)).toMatchObject({ status: "unavailable" });
    },
  );
});
