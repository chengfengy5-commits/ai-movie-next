import { describe, expect, it } from "vitest";
import { CANVAS_SOURCE_CAPABILITY, type CanvasSourceSnapshot } from "../../shared/api/canvasSources";
import type { StoryboardFrame } from "../../shared/api/contracts";
import type { CanvasDocument } from "./canvasDocument";
import {
  bindCanvasSourceDocumentTransition,
  provenCanvasSourceRefMap,
  remapCanvasDocumentSourceRefs,
  resolveCanvasSourceBindingRef,
} from "./canvasSourceBindings";

function snapshot(version: string, frame: Record<string, unknown>, frameIndex = 0): CanvasSourceSnapshot {
  const storyboardId = Array.isArray(frame.storyboard) && typeof frame.storyboard[0] === "string"
    ? frame.storyboard[0]
    : null;
  return {
    capability: CANVAS_SOURCE_CAPABILITY,
    series_id: "series-a",
    chapter_id: "chapter-a",
    version,
    requires_lock: false,
    chapter: {
      id: "chapter-a",
      series_id: "series-a",
      title: "第一章",
      content: [frame as unknown as StoryboardFrame],
      order: 1,
      created_at: "2026-10-24T12:00:00",
      updated_at: "2026-10-24T12:00:00",
      lock: null,
    },
    storyboard_assets: storyboardId === null ? [] : [{
      id: storyboardId,
      series_id: "series-a",
      chapter_id: "chapter-a",
      frame_index: frameIndex,
      name: "分镜",
      description: null,
      image_url: null,
      created_at: "2026-10-24T12:00:00",
      updated_at: "2026-10-24T12:00:00",
    }],
    characters: [],
    scenes: [],
    props: [],
  };
}

function snapshotFrames(version: string, frames: Record<string, unknown>[]): CanvasSourceSnapshot {
  const chapterSnapshot = snapshot(version, frames[0] ?? {});
  const storyboardAssets = frames.flatMap((frame, frameIndex) => {
    const storyboardId = Array.isArray(frame.storyboard) && typeof frame.storyboard[0] === "string"
      ? frame.storyboard[0]
      : null;
    return storyboardId === null ? [] : [{
      id: storyboardId,
      series_id: "series-a",
      chapter_id: "chapter-a",
      frame_index: frameIndex,
      name: `分镜 ${frameIndex + 1}`,
      description: null,
      image_url: null,
      created_at: "2026-10-24T12:00:00",
      updated_at: "2026-10-24T12:00:00",
    }];
  });
  return {
    ...chapterSnapshot,
    chapter: { ...chapterSnapshot.chapter, content: frames as unknown as StoryboardFrame[] },
    storyboard_assets: storyboardAssets,
  };
}

const target = { index: 0, storyboard_id: "board-a" } as const;
const mediaRefs = (version: string) => ({
  preview: `media:${version}:frame:board-a:preview`,
  firstVideo: `media:${version}:frame:board-a:video:0`,
  secondVideo: `media:${version}:frame:board-a:video:1`,
});

describe("canvas source binding migration", () => {
  it("migrates saved duplicate-video slots only when a source command proves their continuity", () => {
    const before = snapshot("version-a", {
      text: "原文", storyboard: ["board-a"], preview: "/preview.png", videos: ["same.mp4", "same.mp4"],
    });
    const after = snapshot("version-b", {
      text: "更新文字", storyboard: ["board-a"], preview: "/preview.png", videos: ["same.mp4", "same.mp4"],
    });
    const refs = provenCanvasSourceRefMap(before, after, {
      command: { type: "frame.update", target, text: "更新文字" }, receipt: null,
    });
    const oldRefs = mediaRefs("version-a");
    const newRefs = mediaRefs("version-b");
    expect(refs).toEqual(new Map([
      [oldRefs.preview, newRefs.preview],
      [oldRefs.firstVideo, newRefs.firstVideo],
      [oldRefs.secondVideo, newRefs.secondVideo],
    ]));

    const document: CanvasDocument = {
      nodeLayouts: {
        [oldRefs.preview]: { x: 100, y: 120, custom: "keep" },
        [oldRefs.firstVideo]: { x: 200, y: 220 },
        [oldRefs.secondVideo]: { x: 300, y: 320 },
        future: { x: 9, y: 10 },
      },
      manualEdges: [
        { id: "preview-edge", sourceRef: "frame:board-a", targetRef: oldRefs.preview, relation: "custom", vendor: 4 },
        { id: "video-edge", sourceRef: oldRefs.secondVideo, targetRef: "character:character-a", relation: "uses" },
        { id: "dangling", sourceRef: "removed-frame", targetRef: oldRefs.firstVideo, relation: "future" },
      ],
      unknown: { retained: true },
    };
    const remapped = remapCanvasDocumentSourceRefs(document, refs);
    expect(remapped.nodeLayouts).toEqual({
      [newRefs.preview]: { x: 100, y: 120, custom: "keep" },
      [newRefs.firstVideo]: { x: 200, y: 220 },
      [newRefs.secondVideo]: { x: 300, y: 320 },
      future: { x: 9, y: 10 },
    });
    expect(remapped.manualEdges).toEqual([
      { id: "preview-edge", sourceRef: "frame:board-a", targetRef: newRefs.preview, relation: "custom", vendor: 4 },
      { id: "video-edge", sourceRef: newRefs.secondVideo, targetRef: "character:character-a", relation: "uses" },
      { id: "dangling", sourceRef: "removed-frame", targetRef: newRefs.firstVideo, relation: "future" },
    ]);
    expect(remapped.unknown).toEqual({ retained: true });
    expect(document.nodeLayouts).toHaveProperty(oldRefs.preview);
  });

  it("persists proven media refs across reopen and refuses to reuse them after an external version change", () => {
    const before = snapshot("version-a", {
      text: "原文", storyboard: ["board-a"], preview: "/preview.png", videos: ["same.mp4", "same.mp4"],
    });
    const after = snapshot("version-b", {
      text: "更新文字", storyboard: ["board-a"], preview: "/preview.png", videos: ["same.mp4", "same.mp4"],
    });
    const beforeRefs = mediaRefs("version-a");
    const document: CanvasDocument = {
      nodeLayouts: {
        [beforeRefs.preview]: { x: 100, y: 120 },
        [beforeRefs.firstVideo]: { x: 200, y: 220 },
        [beforeRefs.secondVideo]: { x: 300, y: 320 },
      },
      manualEdges: [{ id: "edge", sourceRef: beforeRefs.secondVideo, targetRef: "frame:board-a", relation: "manual" }],
      unknown: { retained: true },
    };
    const migrated = bindCanvasSourceDocumentTransition(document, before, after, {
      command: { type: "frame.update", target, text: "更新文字" }, receipt: null,
    });

    expect(migrated.document.canvasSourceBindings).toHaveLength(3);
    expect(migrated.document.nodeLayouts).toMatchObject({
      [migrated.refs.get(beforeRefs.preview)!]: { x: 100, y: 120 },
      [migrated.refs.get(beforeRefs.firstVideo)!]: { x: 200, y: 220 },
      [migrated.refs.get(beforeRefs.secondVideo)!]: { x: 300, y: 320 },
    });
    expect(migrated.document.manualEdges).toEqual([
      { id: "edge", sourceRef: migrated.refs.get(beforeRefs.secondVideo), targetRef: "frame:board-a", relation: "manual" },
    ]);
    expect(migrated.document.unknown).toEqual({ retained: true });

    const reopenedDocument = JSON.parse(JSON.stringify(migrated.document)) as CanvasDocument;
    const nextPreview = `media:version-b:frame:board-a:preview`;
    expect(resolveCanvasSourceBindingRef(reopenedDocument, after, nextPreview))
      .toBe(migrated.refs.get(beforeRefs.preview));
    expect(resolveCanvasSourceBindingRef(reopenedDocument, {
      ...after,
      version: "version-c",
    }, `media:version-c:frame:board-a:preview`)).toBe("media:version-c:frame:board-a:preview");
  });

  it("keeps old bindings when an unrelated source transition reveals video-slot drift", () => {
    const before = snapshot("version-a", {
      text: "原文", storyboard: ["board-a"], preview: "/preview.png", videos: ["one.mp4", "two.mp4"],
    });
    const after = snapshot("version-b", {
      text: "更新文字", storyboard: ["board-a"], preview: "/preview.png", videos: ["two.mp4", "one.mp4"],
    });

    const refs = provenCanvasSourceRefMap(before, after, {
      command: { type: "frame.update", target, text: "更新文字" }, receipt: null,
    });

    expect(refs.size).toBe(0);
  });

  it("maps an append without identifying repeated URLs by value", () => {
    const before = snapshot("version-a", {
      text: "原文", storyboard: ["board-a"], preview: null, videos: ["same.mp4", "same.mp4"],
    });
    const after = snapshot("version-b", {
      text: "原文", storyboard: ["board-a"], preview: null, videos: ["same.mp4", "same.mp4", "same.mp4"],
    });
    const refs = provenCanvasSourceRefMap(before, after, {
      command: { type: "media.append", target, value: "same.mp4" },
      receipt: { token: "proof", kind: "video", target: { storyboard_id: "board-a", index: 0, slot: 2, value: "same.mp4" } },
    });

    expect(refs.get(mediaRefs("version-a").firstVideo)).toBe(mediaRefs("version-b").firstVideo);
    expect(refs.get(mediaRefs("version-a").secondVideo)).toBe(mediaRefs("version-b").secondVideo);
    expect(refs.has(`media:version-a:frame:board-a:video:2`)).toBe(false);
  });

  it("maps an exact no-ID frame update while preserving same-index orphan media rows", () => {
    const before = snapshot("version-a", {
      text: "原文", storyboard: [], preview: "/preview.png", videos: ["same.mp4"],
    });
    const after = snapshot("version-b", {
      text: "更新文字", storyboard: [], preview: "/preview.png", videos: ["same.mp4"],
    });
    const orphan = {
      id: "orphan", series_id: "series-a", chapter_id: "chapter-a", frame_index: 0,
      name: "孤立素材", description: null, image_url: null,
      created_at: "2026-10-24T12:00:00", updated_at: "2026-10-24T12:00:00",
    };
    const beforeWithOrphan = { ...before, storyboard_assets: [orphan] };
    const afterWithOrphan = { ...after, storyboard_assets: [orphan] };
    const refs = provenCanvasSourceRefMap(beforeWithOrphan, afterWithOrphan, {
      command: { type: "frame.update", target: { index: 0, storyboard_id: null }, text: "更新文字" }, receipt: null,
    });

    expect(refs.get("media:version-a:frame-index:version-a:0:preview"))
      .toBe("media:version-b:frame-index:version-b:0:preview");
    expect(refs.get("media:version-a:frame-index:version-a:0:video:0"))
      .toBe("media:version-b:frame-index:version-b:0:video:0");

    const oldFrameRef = "frame-index:version-a:0";
    const oldPreviewRef = "media:version-a:frame-index:version-a:0:preview";
    const document: CanvasDocument = {
      nodeLayouts: { [oldFrameRef]: { x: 900, y: 60 }, [oldPreviewRef]: { x: 1200, y: 80 } },
      manualEdges: [{ id: "edge", sourceRef: oldFrameRef, targetRef: oldPreviewRef, relation: "manual" }],
    };
    const migrated = bindCanvasSourceDocumentTransition(document, beforeWithOrphan, afterWithOrphan, {
      command: { type: "frame.update", target: { index: 0, storyboard_id: null }, text: "更新文字" }, receipt: null,
    });
    expect(migrated.document.nodeLayouts).toMatchObject({
      [migrated.refs.get(oldFrameRef)!]: { x: 900, y: 60 },
      [migrated.refs.get(oldPreviewRef)!]: { x: 1200, y: 80 },
    });
    expect(resolveCanvasSourceBindingRef(migrated.document, afterWithOrphan, "frame-index:version-b:0"))
      .toBe(migrated.refs.get(oldFrameRef));
  });

  it("keeps media layout on the same identified frame when a proven insert shifts its index", () => {
    const before = snapshotFrames("version-a", [
      { text: "A", storyboard: ["board-a"], preview: "/a.png", videos: ["same.mp4"] },
      { text: "B", storyboard: ["board-b"], preview: null, videos: ["same.mp4", "same.mp4"] },
    ]);
    const insertedId = "board-new";
    const after = snapshotFrames("version-b", [
      { text: "A", storyboard: ["board-a"], preview: "/a.png", videos: ["same.mp4"] },
      { text: "inserted", storyboard: [insertedId], preview: null, videos: [] },
      { text: "B", storyboard: ["board-b"], preview: null, videos: ["same.mp4", "same.mp4"] },
    ]);
    const refs = provenCanvasSourceRefMap(before, after, {
      command: {
        type: "frame.insert",
        after: { index: 0, storyboard_id: "board-a" },
        frame: { text: "inserted" },
      },
      receipt: {
        token: "insert-proof",
        kind: "frame",
        target: { id: insertedId, storyboard_id: insertedId, index: 1 },
      },
    });

    expect(refs.get("media:version-a:frame:board-b:video:0"))
      .toBe("media:version-b:frame:board-b:video:0");
    expect(refs.get("media:version-a:frame:board-b:video:1"))
      .toBe("media:version-b:frame:board-b:video:1");
  });

  it("does not migrate a shifted frame slot when its videos drift during an unrelated frame deletion", () => {
    const before = snapshotFrames("version-a", [
      { text: "A", storyboard: ["board-a"], preview: null, videos: ["same.mp4"] },
      { text: "B", storyboard: ["board-b"], preview: null, videos: ["one.mp4", "two.mp4"] },
    ]);
    const after = snapshotFrames("version-b", [
      { text: "B", storyboard: ["board-b"], preview: null, videos: ["two.mp4", "one.mp4"] },
    ]);
    const refs = provenCanvasSourceRefMap(before, after, {
      command: { type: "frame.delete", target: { index: 0, storyboard_id: "board-a" } },
      receipt: null,
    });

    expect(refs.has("media:version-a:frame:board-b:video:0")).toBe(false);
    expect(refs.has("media:version-a:frame:board-b:video:1")).toBe(false);
  });
});
