import { describe, expect, it } from "vitest";
import type { Character, Prop, Scene } from "../../shared/api/contracts";
import { searchAssetLibraryItems } from "./assetLibrarySearch";

const timestamp = "2026-10-01T00:00:00Z";

function character(overrides: Partial<Character> = {}): Character {
  return {
    id: "character-id",
    series_id: "series-id",
    name: "林岚",
    gender: null,
    age: null,
    role: null,
    appearance: null,
    description: null,
    image_url: null,
    audio_url: null,
    voice_ref: null,
    aliases: ["林编辑", "阿岚", "   "],
    canonical_key: "private-character-key",
    created_at: timestamp,
    updated_at: timestamp,
    ...overrides,
  };
}

function scene(overrides: Partial<Scene> = {}): Scene {
  return {
    id: "scene-id",
    series_id: "series-id",
    title: "旧街清晨",
    description: "private description text",
    image_url: null,
    aliases: ["旧街早晨", "  "],
    canonical_key: "private-scene-key",
    created_at: timestamp,
    updated_at: timestamp,
    ...overrides,
  };
}

function prop(overrides: Partial<Prop> = {}): Prop {
  return {
    id: "prop-id",
    series_id: "series-id",
    name: "黄铜钥匙",
    description: "private prop description",
    image_url: null,
    aliases: ["旧铜钥匙"],
    canonical_key: "private-prop-key",
    created_at: timestamp,
    updated_at: timestamp,
    ...overrides,
  };
}

describe("searchAssetLibraryItems", () => {
  it.each([
    ["characters", [character()], " 林编 ", "character-id"],
    ["scenes", [scene()], " 旧街早晨 ", "scene-id"],
    ["props", [prop()], "黄铜钥匙", "prop-id"],
  ] as const)("matches the original name/title or visible alias for %s", (category, items, query, expectedId) => {
    const matches = searchAssetLibraryItems(category, items, query);

    expect(matches).toHaveLength(1);
    expect(matches[0]?.item.id).toBe(expectedId);
    expect(matches[0]?.originalIndex).toBe(0);
  });

  it("uses case-insensitive literal substrings and preserves original order, index, and item identity", () => {
    const first = character({ id: "first", name: "R2 (Draft) [A].*" });
    const middle = character({ id: "middle", name: "Unrelated" });
    const last = character({ id: "last", name: "Third", aliases: ["R2 (draft) [A].*"] });
    const items = [first, middle, last];
    const before = [...items];

    const matches = searchAssetLibraryItems("characters", items, "  r2 (DRAFT) [a].*  ");

    expect(matches.map((entry) => entry.originalIndex)).toEqual([0, 2]);
    expect(matches[0]?.item).toBe(first);
    expect(matches[1]?.item).toBe(last);
    expect(items).toEqual(before);
    expect(items[0]).toBe(first);
  });

  it("does not search display placeholders, descriptions, character details, or canonical keys", () => {
    const unnamed = character({
      name: "",
      aliases: null,
      description: "description-only-needle",
      role: "role-only-needle",
      appearance: "appearance-only-needle",
      canonical_key: "canonical-only-needle",
    });

    expect(searchAssetLibraryItems("characters", [unnamed], "未命名角色")).toHaveLength(0);
    expect(searchAssetLibraryItems("characters", [unnamed], "description-only-needle")).toHaveLength(0);
    expect(searchAssetLibraryItems("characters", [unnamed], "role-only-needle")).toHaveLength(0);
    expect(searchAssetLibraryItems("characters", [unnamed], "appearance-only-needle")).toHaveLength(0);
    expect(searchAssetLibraryItems("characters", [unnamed], "canonical-only-needle")).toHaveLength(0);
  });

  it("shows every original entry for an empty or whitespace-only query", () => {
    const first = scene({ id: "first" });
    const second = scene({ id: "second", title: "" });
    const items = [first, second];

    const empty = searchAssetLibraryItems("scenes", items, "");
    const whitespace = searchAssetLibraryItems("scenes", items, " \t ");

    expect(empty.map((entry) => entry.originalIndex)).toEqual([0, 1]);
    expect(whitespace.map((entry) => entry.originalIndex)).toEqual([0, 1]);
    expect(empty[0]?.item).toBe(first);
    expect(empty[1]?.item).toBe(second);
  });
});
