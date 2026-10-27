import { describe, expect, it } from "vitest";
import type { Character, FusedAsset, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import { projectFrameResources, type FrameResourceDirectories } from "./frameResourceProjection";

const frameAsset: StoryboardAsset = {
  id: "frame-a", series_id: "series-a", chapter_id: "chapter-a", frame_index: 0,
  name: "第一镜", description: null, image_url: null, created_at: "", updated_at: "",
};
const character: Character = {
  id: "character-a", series_id: "series-a", description: null, image_url: null, created_at: "", updated_at: "",
  name: "主角", aliases: null, canonical_key: null, gender: null, age: null, role: null, appearance: null,
  audio_url: null, voice_ref: null,
};
const fused: FusedAsset = {
  id: "fused-a", series_id: "series-a", member_type: "", members: [], name: "融合图", image_url: null,
  size: null, status: "completed", error_message: null, created_at: "", updated_at: "",
};

const directories: FrameResourceDirectories = {
  storyboard: { status: "ready", items: [frameAsset] },
  characters: { status: "ready", items: [character] },
  scenes: { status: "ready", items: [] },
  props: { status: "ready", items: [] },
  fused: { status: "ready", items: [fused] },
};

describe("projectFrameResources", () => {
  it("projects five real directories in storyboard-first order and does not invent missing IDs", () => {
    const frame: StoryboardFrame = {
      storyboard: ["frame-a"], character: ["character-a", "missing-character"], fused: ["fused-a"],
    };
    const groups = projectFrameResources(frame, 0, "chapter-a", "series-a", [frame], directories);
    expect(groups.map((group) => group.category)).toEqual(["storyboard", "characters", "scenes", "props", "fused"]);
    expect(groups[0]?.rows[0]).toMatchObject({ state: "ready", target: { kind: "frame", storyboardAssetId: "frame-a" } });
    expect(groups[1]?.rows.map((row) => row.state)).toEqual(["ready", "missing"]);
    expect(groups[1]?.rows[1]?.target).toBeNull();
    expect(groups[4]?.rows[0]?.target).toMatchObject({ kind: "asset", category: "fused", assetId: "fused-a" });
  });

  it("rejects ambiguous and moved storyboard references and filters failed fused entries", () => {
    const frame: StoryboardFrame = { storyboard: ["frame-a"], fused: ["fused-failed"] };
    const failed: FusedAsset = { ...fused, id: "fused-failed", status: "failed" };
    const withFailed = { ...directories, fused: { status: "ready" as const, items: [failed] } };
    const moved = projectFrameResources(frame, 1, "chapter-a", "series-a", [frame, frame], directories);
    expect(moved[0]?.rows[0]?.state).toBe("missing");
    const filtered = projectFrameResources(frame, 0, "chapter-a", "series-a", [frame], withFailed);
    expect(filtered[4]?.rows[0]?.state).toBe("missing");
    expect(filtered[4]?.rows[0]?.target).toBeNull();
  });
});
