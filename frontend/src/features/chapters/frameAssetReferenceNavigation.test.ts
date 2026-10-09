import type { Character, StoryboardFrame } from "../../shared/api/contracts";
import { describe, expect, it } from "vitest";
import { projectFrameAssetReferences } from "./frameAssetReferences";
import { resolveFrameAssetReferenceNavigationId } from "./frameAssetReferenceNavigation";

const seriesId = "reference-navigation-series";

function character(overrides: Partial<Character> = {}): Character {
  return {
    id: "character-id",
    series_id: seriesId,
    name: "同名角色",
    gender: null,
    age: null,
    role: null,
    appearance: null,
    description: null,
    image_url: null,
    audio_url: null,
    voice_ref: null,
    aliases: null,
    canonical_key: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

function frame(characterRefs: unknown): StoryboardFrame {
  return { character: characterRefs };
}

describe("frame asset reference navigation identity", () => {
  it("maps each resolved duplicate row to its raw ID at the original reference index", () => {
    const source = frame(["character-id", "character-id"]);
    const directory = { category: "characters" as const, items: [character()] };
    const projection = projectFrameAssetReferences(source, "characters", seriesId, directory.items);
    if (projection.status !== "references") {
      throw new Error("Expected resolved reference rows.");
    }
    projection.rows[0]!.key = "display-key-does-not-encode-an-index";
    projection.rows[1]!.key = "another-display-key";

    expect(resolveFrameAssetReferenceNavigationId(source, "characters", seriesId, directory, projection, 0)).toBe("character-id");
    expect(resolveFrameAssetReferenceNavigationId(source, "characters", seriesId, directory, projection, 1)).toBe("character-id");
  });

  it("requires a resolved projection row and one same-series record in the ready category directory", () => {
    const source = frame(["character-id"]);
    const uniqueDirectory = { category: "characters" as const, items: [character()] };
    const projection = projectFrameAssetReferences(source, "characters", seriesId, uniqueDirectory.items);

    expect(resolveFrameAssetReferenceNavigationId(source, "characters", seriesId, uniqueDirectory, projection, 0)).toBe("character-id");
    expect(resolveFrameAssetReferenceNavigationId(source, "characters", seriesId, {
      category: "characters",
      items: [character(), character({ name: "重复名称，不同对象" })],
    }, projection, 0)).toBeNull();
    expect(resolveFrameAssetReferenceNavigationId(source, "characters", seriesId, {
      category: "characters",
      items: [character({ series_id: "another-series" })],
    }, projection, 0)).toBeNull();
    expect(resolveFrameAssetReferenceNavigationId(source, "characters", seriesId, {
      category: "characters",
      items: [character(), character({ series_id: "another-series" })],
    }, projection, 0)).toBeNull();
    expect(resolveFrameAssetReferenceNavigationId(source, "scenes", seriesId, uniqueDirectory, projection, 0)).toBeNull();
    expect(resolveFrameAssetReferenceNavigationId(source, "characters", seriesId, uniqueDirectory, projection, 3)).toBeNull();
  });

  it("does not navigate by matching names or malformed source rows", () => {
    for (const value of [["同名角色"], [" "]] as const) {
      const source = frame(value);
      const directory = { category: "characters" as const, items: [character()] };
      const projection = projectFrameAssetReferences(source, "characters", seriesId, directory.items);
      expect(resolveFrameAssetReferenceNavigationId(source, "characters", seriesId, directory, projection, 0)).toBeNull();
    }

    const source = frame(["character-id"]);
    const directory = { category: "characters" as const, items: [character()] };
    expect(resolveFrameAssetReferenceNavigationId(source, "characters", seriesId, directory, {
      status: "unreadable",
      rows: [],
    }, 0)).toBeNull();
  });
});
