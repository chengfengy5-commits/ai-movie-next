import type { Character, Prop, Scene, StoryboardFrame } from "../../shared/api/contracts";
import { describe, expect, it } from "vitest";
import {
  hasValidFrameAssetReference,
  projectFrameAssetReferences,
  type FrameAssetReferenceCategory,
} from "./frameAssetReferences";

const seriesId = "series-reference-test";

function character(overrides: Partial<Character> = {}): Character {
  return {
    id: "same-id",
    series_id: seriesId,
    name: "沈照",
    aliases: ["阿照", ""],
    canonical_key: "not-an-identity",
    description: "<em>守门人</em>",
    image_url: "https://media.invalid/person.png",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    gender: "女",
    age: "二十岁",
    role: "守门人",
    appearance: "青衣",
    audio_url: "https://media.invalid/audio.mp3",
    voice_ref: "private voice ref",
    ...overrides,
  };
}

function scene(overrides: Partial<Scene> = {}): Scene {
  return {
    id: "same-id",
    series_id: seriesId,
    title: "盐仓码头",
    aliases: null,
    canonical_key: "another-non-identity",
    description: null,
    image_url: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

function prop(overrides: Partial<Prop> = {}): Prop {
  return {
    id: "prop-a",
    series_id: seriesId,
    name: "旧铜钥匙",
    aliases: [],
    canonical_key: null,
    description: "<script>bad()</script>",
    image_url: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

function frame(overrides: Record<string, unknown> = {}): StoryboardFrame {
  return { text: "镜头文字", ...overrides };
}

function project(
  value: StoryboardFrame,
  category: FrameAssetReferenceCategory,
  directory: Array<Character | Scene | Prop>,
) {
  return projectFrameAssetReferences(value, category, seriesId, directory);
}

describe("frame asset reference projection", () => {
  it("treats a missing field, null, and an empty list as unlinked without valid references", () => {
    for (const value of [frame(), frame({ character: null }), frame({ character: [] })]) {
      expect(project(value, "characters", [character()])).toEqual({ status: "unlinked", rows: [] });
      expect(hasValidFrameAssetReference(value, "characters")).toBe(false);
    }
  });

  it("keeps invalid outer structure separate from an empty reference list", () => {
    for (const value of ["same-id", { id: "same-id" }, 4]) {
      const result = project(frame({ character: value }), "characters", [character()]);
      expect(result).toEqual({ status: "unreadable", rows: [] });
      expect(hasValidFrameAssetReference(frame({ character: value }), "characters")).toBe(false);
    }
  });

  it("preserves mixed reference order and duplicates while isolating malformed rows", () => {
    const result = project(
      frame({ character: ["same-id", " ", 7, "missing-id", "same-id"] }),
      "characters",
      [character()],
    );

    expect(result.status).toBe("references");
    if (result.status !== "references") {
      throw new Error("Expected parsed reference rows.");
    }
    expect(result.hasValidReferences).toBe(true);
    expect(result.rows.map((row) => row.status)).toEqual([
      "resolved",
      "invalid",
      "invalid",
      "unmatched",
      "resolved",
    ]);
    expect(result.rows.map((row) => row.name)).toEqual(["沈照", null, null, null, "沈照"]);
    expect(JSON.stringify(result)).not.toContain("same-id");
    expect(JSON.stringify(result)).not.toContain("missing-id");
  });

  it("uses category-scoped exact IDs and does not resolve aliases or canonical names", () => {
    const sameIdCharacters = [character()];
    const result = project(frame({ scene: ["same-id", "盐仓码头", "another-non-identity"] }), "scenes", [scene()]);

    expect(result.status).toBe("references");
    if (result.status !== "references") {
      throw new Error("Expected parsed scene references.");
    }
    expect(result.rows.map((row) => row.status)).toEqual(["resolved", "unmatched", "unmatched"]);
    expect(result.rows[0]?.name).toBe("盐仓码头");
    expect(project(frame({ character: ["same-id"] }), "characters", sameIdCharacters)).toMatchObject({
      status: "references",
      rows: [{ status: "resolved", name: "沈照" }],
    });
  });

  it("rejects duplicate catalogue IDs and entries owned by another series", () => {
    const duplicate = project(frame({ character: ["same-id"] }), "characters", [
      character(),
      character({ name: "重复素材" }),
    ]);
    const wrongSeries = project(frame({ character: ["same-id"] }), "characters", [
      character({ series_id: "another-series" }),
    ]);

    expect(duplicate).toMatchObject({ status: "references", rows: [{ status: "unmatched", name: null }] });
    expect(wrongSeries).toMatchObject({ status: "references", rows: [{ status: "unmatched", name: null }] });
  });

  it("uses safe text details, character traits, and a placeholder for an empty name", () => {
    const characters = project(frame({ character: ["same-id"] }), "characters", [character()]);
    const props = project(frame({ prop: ["prop-a"] }), "props", [prop({ name: "" })]);

    expect(characters).toMatchObject({
      status: "references",
      rows: [{
        status: "resolved",
        name: "沈照",
        aliases: ["阿照"],
        description: "<em>守门人</em>",
        traits: [
          { label: "身份", value: "守门人" },
          { label: "性别", value: "女" },
          { label: "年龄", value: "二十岁" },
          { label: "外观", value: "青衣" },
        ],
      }],
    });
    expect(props).toMatchObject({
      status: "references",
      rows: [{ status: "resolved", name: "未命名道具", description: "<script>bad()</script>" }],
    });
    expect(JSON.stringify(characters)).not.toContain("audio.mp3");
    expect(JSON.stringify(characters)).not.toContain("private voice ref");
    expect(JSON.stringify(characters)).not.toContain("canonical_key");
  });
});
