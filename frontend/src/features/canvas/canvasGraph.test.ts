import { describe, expect, it } from "vitest";
import type { Character, Chapter, StoryboardAsset } from "../../shared/api/contracts";
import { buildCanvasGraph } from "./canvasGraph";

const chapter: Chapter = {
  id: "chapter-a",
  series_id: "series-a",
  title: "第一章",
  order: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  lock: null,
  content: [{
    text: "当前分镜",
    storyboard: ["storyboard-a"],
    character: ["character-a"],
    scene: [],
    prop: [],
    preview: "/uploads/preview.png",
    videos: ["/uploads/video.mp4"],
  }],
};

const character = {
  id: "character-a",
  series_id: "series-a",
  name: "林澜",
  description: "当前章节引用",
  image_url: "/uploads/character.png",
} as Character;

const storyboard: StoryboardAsset = {
  id: "storyboard-a",
  series_id: "series-a",
  chapter_id: "chapter-a",
  frame_index: 0,
  name: "夜雨构图",
  description: null,
  image_url: "/uploads/storyboard.png",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

describe("chapter canvas graph projection", () => {
  it("accepts a unique first storyboard ID and exact null targets with same-index orphan rows", () => {
    const withExtraReference = {
      ...chapter,
      content: [{ ...chapter.content![0], storyboard: ["storyboard-a", "legacy-reference"] }],
    };
    const firstIdGraph = buildCanvasGraph(withExtraReference, [storyboard], [character], [], [], {}, "version-a");
    expect(firstIdGraph.nodes.find((node) => node.kind === "frame"))
      .toMatchObject({ ref: "frame:storyboard-a", sourceTarget: { index: 0, storyboard_id: "storyboard-a" } });

    const noIdFrame = {
      ...chapter,
      content: [{ ...chapter.content![0], storyboard: [null, ""] as unknown as string[] }],
    };
    const orphan = { ...storyboard, id: "orphan-board", frame_index: 0 };
    const noIdGraph = buildCanvasGraph(noIdFrame, [orphan], [character], [], [], {}, "version-a");
    expect(noIdGraph.nodes.find((node) => node.kind === "frame"))
      .toMatchObject({ ref: "frame-index:version-a:0", sourceTarget: { index: 0, storyboard_id: null } });

    for (const references of [[" "], [null, "\t"]]) {
      const whitespaceIdentity = {
        ...chapter,
        content: [{ ...chapter.content![0], storyboard: references as unknown as string[] }],
      };
      const graph = buildCanvasGraph(whitespaceIdentity, [], [character], [], [], {}, "version-a");
      expect(graph.nodes.find((node) => node.kind === "frame"))
        .toMatchObject({ ref: "frame-ambiguous:version-a:0", sourceTarget: null });
    }
  });

  it("uses only exact persisted source bindings and leaves a changed version unmatched", () => {
    const sourceVersion = "source-version-b";
    const boundChapter = {
      ...chapter,
      content: [{ ...chapter.content![0]!, videos: ["same.mp4", "other.mp4"] }],
    };
    const videoBinding = {
      ref: "canvas-source-binding:video-zero",
      seriesId: chapter.series_id,
      chapterId: chapter.id,
      sourceVersion,
      sourceRef: `media:${sourceVersion}:frame:storyboard-a:video:0`,
    };
    const document = {
      nodeLayouts: {
        [videoBinding.ref]: { x: 2100, y: 320 },
      },
      canvasSourceBindings: [videoBinding],
    };
    const current = buildCanvasGraph(boundChapter, [storyboard], [character], [], [], document, sourceVersion);
    expect(current.nodes.find((node) => node.kind === "video" && node.detail === "same.mp4"))
      .toMatchObject({ ref: videoBinding.ref, x: 2100, y: 320 });

    const externallyChanged = buildCanvasGraph({
      ...boundChapter,
      content: [{ ...boundChapter.content![0]!, videos: ["other.mp4", "same.mp4"] }],
    }, [storyboard], [character], [], [], document, "source-version-c");
    expect(externallyChanged.nodes.find((node) => node.kind === "video" && node.detail === "same.mp4"))
      .toMatchObject({ ref: "media:source-version-c:frame:storyboard-a:video:1", x: 1540 });
  });

  it("projects exact current sources and legacy index layouts without guessing identities", () => {
    const graph = buildCanvasGraph(
      chapter,
      [storyboard],
      [character],
      [],
      [],
      {
        nodeLayouts: [
          { refId: "frame:0", x: 0, y: -4 },
          { refId: "storyboard:storyboard-a", x: 900, y: 0 },
          { refId: "media:0:0", x: 1100, y: 20 },
        ],
        notes: [{ id: "note-a", text: "备注", x: 0, y: 0 }],
        manualEdges: [{ id: "edge-a", sourceRef: "deleted", targetRef: "frame:0", relation: "unknown" }],
      },
    );
    const frame = graph.nodes.find((node) => node.ref === "frame:0");
    const media = graph.nodes.find((node) => node.ref === "storyboard:storyboard-a");
    const preview = graph.nodes.find((node) => node.ref === "media:0:0");
    expect(frame?.x).toBe(0);
    expect(frame?.y).toBe(-4);
    expect(media?.x).toBe(900);
    expect(media?.imageUrl).toBe("/uploads/storyboard.png");
    expect(preview?.x).toBe(1100);
    expect(preview?.imageUrl).toBe("/uploads/preview.png");
    expect(graph.nodes.find((node) => node.ref === "character:character-a"))
      .toMatchObject({ kind: "character", itemId: "character-a" });
    expect(graph.nodes.some((node) => node.ref === "media:0:0")).toBe(true);
    expect(graph.nodes.some((node) => node.ref === "media:0:1")).toBe(true);
    expect(graph.nodes.some((node) => node.ref === "note-a")).toBe(true);
    expect(graph.edges.some((edge) => edge.sourceRef === "character:character-a" && edge.relation === "uses")).toBe(true);
    expect(graph.edges.some((edge) => edge.sourceRef === "frame:0" && edge.targetRef === "storyboard:storyboard-a")).toBe(true);
    expect(graph.edges.some((edge) => edge.id === "edge-a")).toBe(false);
  });

  it("projects unique legacy index layouts and edges onto current source refs without changing targets", () => {
    const sourceVersion = "8be1499c4eb8b1a1f8e20d020cd52796e938726f0a1889cfecc07641d8d28a57";
    const sourceChapter = {
      ...chapter,
      content: [{ ...chapter.content![0]!, preview: "/uploads/preview.png", videos: ["same.mp4", "same.mp4"] }],
    };
    const graph = buildCanvasGraph(sourceChapter, [storyboard], [character], [], [], {
      nodeLayouts: [
        { refId: "frame:0", x: 0, y: -4, custom: "legacy-frame" },
        { refId: "media:0:0", x: -120, y: 110, custom: "legacy-preview" },
        { refId: "media:0:1", x: 380, y: -20, custom: "legacy-video-zero" },
        { refId: "media:0:2", x: 720, y: 340, custom: "legacy-video-one" },
      ],
      manualEdges: [
        { id: "frame-preview", sourceRef: "frame:0", targetRef: "media:0:0", relation: "manual" },
        { id: "video-frame", sourceRef: "media:0:2", targetRef: "frame:0", relation: "manual" },
        { id: "dangling", sourceRef: "future:source", targetRef: "future:target", relation: "future" },
      ],
    }, sourceVersion);
    const frame = graph.nodes.find((node) => node.kind === "frame");
    const preview = graph.nodes.find((node) => node.kind === "preview");
    const videos = graph.nodes.filter((node) => node.kind === "video");

    expect(frame).toMatchObject({
      ref: "frame:storyboard-a",
      layoutRef: "frame:0",
      sourceTarget: { index: 0, storyboard_id: "storyboard-a" },
      x: 0,
      y: -4,
    });
    expect(preview).toMatchObject({
      ref: `media:${sourceVersion}:frame:storyboard-a:preview`,
      layoutRef: "media:0:0",
      x: -120,
      y: 110,
    });
    expect(videos).toEqual(expect.arrayContaining([
      expect.objectContaining({ ref: `media:${sourceVersion}:frame:storyboard-a:video:0`, layoutRef: "media:0:1", x: 380, y: -20 }),
      expect.objectContaining({ ref: `media:${sourceVersion}:frame:storyboard-a:video:1`, layoutRef: "media:0:2", x: 720, y: 340 }),
    ]));
    expect(graph.edges).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "frame-preview", sourceRef: "frame:storyboard-a", targetRef: preview?.ref, manual: true }),
      expect.objectContaining({ id: "video-frame", sourceRef: videos[1]?.ref, targetRef: "frame:storyboard-a", manual: true }),
    ]));
    expect(graph.edges.some((edge) => edge.id === "dangling")).toBe(false);
  });

  it("keeps a numeric stable frame ID distinct from a colliding legacy frame index", () => {
    const sourceVersion = "8be1499c4eb8b1a1f8e20d020cd52796e938726f0a1889cfecc07641d8d28a57";
    const twoFrames = {
      ...chapter,
      content: [
        { ...chapter.content![0]!, storyboard: ["storyboard-a"] },
        { ...chapter.content![0]!, storyboard: ["0"], text: "数字稳定 ID" },
      ],
    };
    const numericStoryboard = { ...storyboard, id: "0", frame_index: 1, name: "数字稳定 ID" };
    const graph = buildCanvasGraph(twoFrames, [storyboard, numericStoryboard], [], [], [], {
      nodeLayouts: { "frame:0": { x: 120, y: -20 } },
      manualEdges: [{ id: "numeric-edge", sourceRef: "frame:0", targetRef: "media:0:0", relation: "manual" }],
    }, sourceVersion);
    const frames = graph.nodes.filter((node) => node.kind === "frame");
    const firstFrame = frames.find((node) => node.frameIndex === 0);
    const numericFrame = frames.find((node) => node.frameIndex === 1);

    expect(firstFrame).toMatchObject({ ref: "frame:storyboard-a", x: 940, y: 60 });
    expect(numericFrame).toMatchObject({ ref: "frame:0", x: 120, y: -20 });
    expect(graph.edges).toContainEqual(expect.objectContaining({
      id: "numeric-edge",
      sourceRef: "frame:0",
      targetRef: `media:${sourceVersion}:frame:storyboard-a:preview`,
      manual: true,
    }));
  });

  it("keeps an existing stable media ref distinct from a colliding legacy media slot", () => {
    const sourceVersion = "8be1499c4eb8b1a1f8e20d020cd52796e938726f0a1889cfecc07641d8d28a57";
    const twoFrames = {
      ...chapter,
      content: [
        { ...chapter.content![0]!, storyboard: ["storyboard-a"], preview: "/uploads/first.png" },
        { ...chapter.content![0]!, storyboard: ["board-b"], preview: "/uploads/second.png" },
      ],
    };
    const secondStoryboard = { ...storyboard, id: "board-b", frame_index: 1, name: "第二镜" };
    const stableMediaBinding = {
      ref: "media:0:0",
      seriesId: chapter.series_id,
      chapterId: chapter.id,
      sourceVersion,
      sourceRef: `media:${sourceVersion}:frame:board-b:preview`,
    };
    const graph = buildCanvasGraph(twoFrames, [storyboard, secondStoryboard], [], [], [], {
      nodeLayouts: { "media:0:0": { x: 123, y: -44 } },
      canvasSourceBindings: [stableMediaBinding],
      manualEdges: [{ id: "stable-media-edge", sourceRef: "media:0:0", targetRef: "frame:board-b", relation: "manual" }],
    }, sourceVersion);
    const previews = graph.nodes.filter((node) => node.kind === "preview");
    const firstPreview = previews.find((node) => node.frameIndex === 0);
    const secondPreview = previews.find((node) => node.frameIndex === 1);

    expect(firstPreview).toMatchObject({
      ref: `media:${sourceVersion}:frame:storyboard-a:preview`,
      x: 1540,
      y: 60,
    });
    expect(secondPreview).toMatchObject({ ref: "media:0:0", x: 123, y: -44 });
    expect(graph.edges).toContainEqual(expect.objectContaining({
      id: "stable-media-edge",
      sourceRef: "media:0:0",
      targetRef: "frame:board-b",
      manual: true,
    }));
  });

  it("omits ambiguous source identities while leaving persisted data untouched", () => {
    const graph = buildCanvasGraph(
      chapter,
      [storyboard, { ...storyboard, name: "重复故事板" }],
      [character, character],
      [],
      [],
      { nodes: [{ id: "unknown" }], manualEdges: [{ id: "dangling", sourceRef: "a", targetRef: "b", relation: "future" }] },
    );
    expect(graph.nodes.some((node) => node.kind === "media")).toBe(false);
    expect(graph.nodes.some((node) => node.kind === "character")).toBe(false);
    expect(graph.nodes.some((node) => node.kind === "frame")).toBe(true);
  });

  it("restores the legacy persisted refs, labels, media indexes, and manual edges", () => {
    const legacyChapter = {
      ...chapter,
      content: [{
        ...chapter.content![0],
        character: ["character-a"],
        scene: [],
        prop: [],
        storyboard: ["storyboard-a"],
        preview: "/uploads/preview.png",
        videos: ["/uploads/video.mp4"],
      }],
    };
    const graph = buildCanvasGraph(
      legacyChapter,
      [storyboard],
      [character],
      [],
      [],
      {
        nodeLayouts: [
          { refId: "storyboard:storyboard-a", x: 900, y: 0 },
          { refId: "media:0:0", x: 1200, y: 10 },
          { refId: "media:0:1", x: 1500, y: 20 },
        ],
        notes: [{ id: "note-a", text: "便签", x: 0, y: 0 }],
        cards: [{ id: "card-a", type: "chapter-card", label: "旧章节卡" }],
        manualEdges: [{ id: "old-edge", sourceRef: "note-a", targetRef: "card:card-a" }],
        hiddenRelations: ["uses", "future-relationship"],
      },
    );
    expect(graph.nodes.find((node) => node.ref === "storyboard:storyboard-a")?.x).toBe(900);
    expect(graph.nodes.find((node) => node.ref === "media:0:0")?.kind).toBe("preview");
    expect(graph.nodes.find((node) => node.ref === "media:0:1")?.kind).toBe("video");
    expect(graph.nodes.find((node) => node.ref === "note-a")?.detail).toBe("便签");
    expect(graph.nodes.find((node) => node.ref === "card:card-a")?.title).toBe("章节卡片");
    expect(graph.nodes.find((node) => node.ref === "card:card-a")?.detail).toBe("旧章节卡");
    expect(graph.edges.some((edge) => edge.id === "old-edge" && edge.relation === "manual")).toBe(true);
    expect(graph.edges.some((edge) => edge.relation === "uses")).toBe(false);
  });

  it("restores video refs from the legacy media sequence when a preview is absent", () => {
    const noPreviewChapter = {
      ...chapter,
      content: [{ ...chapter.content![0], preview: null, videos: ["/uploads/first.mp4", "/uploads/second.mp4"] }],
    };
    const graph = buildCanvasGraph(noPreviewChapter, [], [character], [], [], {
      nodeLayouts: [
        { refId: "media:0:0", x: 0, y: 12 },
        { refId: "media:0:1", x: 24, y: 36 },
      ],
      manualEdges: [{ id: "legacy-video-edge", sourceRef: "media:0:0", targetRef: "frame:0" }],
    });

    expect(graph.nodes.find((node) => node.ref === "media:0:0"))
      .toMatchObject({ kind: "video", detail: "/uploads/first.mp4", x: 0, y: 12 });
    expect(graph.nodes.find((node) => node.ref === "media:0:1"))
      .toMatchObject({ kind: "video", detail: "/uploads/second.mp4", x: 24, y: 36 });
    expect(graph.edges.some((edge) => edge.id === "legacy-video-edge" && edge.manual)).toBe(true);
  });

  it("keeps persisted media refs when a preview URL is not renderable and restores source references", () => {
    const sourceFrame = {
      ...chapter.content![0],
      character: ["character-a"],
      scene: ["scene-a"],
      prop: ["prop-a"],
      preview: "data:image/png;base64,AAAA",
      videos: ["/uploads/video.mp4"],
    };
    const sourceChapter = { ...chapter, content: [sourceFrame] };
    const scene = { id: "scene-a", series_id: "series-a", title: "雨巷", description: "scene", image_url: null } as unknown as import("../../shared/api/contracts").Scene;
    const prop = { id: "prop-a", series_id: "series-a", name: "信件", description: "prop", image_url: null } as unknown as import("../../shared/api/contracts").Prop;
    const graph = buildCanvasGraph(sourceChapter, [], [character], [scene], [prop], {
      nodeLayouts: [
        { refId: "media:0:0", x: 0, y: 0 },
        { refId: "media:0:1", x: 20, y: 30 },
      ],
    });

    expect(graph.nodes.find((node) => node.ref === "media:0:0"))
      .toMatchObject({ kind: "preview", imageUrl: null, x: 0, y: 0 });
    expect(graph.nodes.find((node) => node.ref === "media:0:1"))
      .toMatchObject({ kind: "video", x: 20, y: 30 });
    const sourceRefs = ["character:character-a", "scene:scene-a", "prop:prop-a"];
    for (const sourceRef of sourceRefs) {
      expect(graph.edges.some((edge) => edge.sourceRef === sourceRef && edge.targetRef === "media:0:0"
        && edge.relation === "reference")).toBe(true);
    }
    expect(graph.edges.some((edge) => edge.sourceRef === "frame:0" && edge.targetRef === "media:0:0"
      && edge.relation === "generates")).toBe(true);
  });

  it.each([
    { label: "null preview", preview: null, expectedVideoOrdinal: 2 },
    { label: "empty preview", preview: "", expectedVideoOrdinal: 2 },
    { label: "whitespace preview", preview: " \t", expectedVideoOrdinal: 3 },
    { label: "renderable preview", preview: "/uploads/preview.png", expectedVideoOrdinal: 3 },
    { label: "non-renderable preview", preview: "data:image/png;base64,AAAA", expectedVideoOrdinal: 3 },
  ])("preserves legacy media ordinals for a $label", ({ preview, expectedVideoOrdinal }) => {
    const videoUrl = "/uploads/real.mp4";
    const sourceFrame = {
      ...chapter.content![0],
      preview,
      videos: ["", " \t", null, 7, videoUrl],
    };
    const sourceChapter = { ...chapter, content: [sourceFrame] };
    const originalChapter = structuredClone(sourceChapter);
    const expectedVideoRef = "media:0:" + expectedVideoOrdinal;
    const expectedY = 28 + expectedVideoOrdinal;
    const document = {
      nodeLayouts: [
        { refId: "media:0:0", x: 880, y: 10 },
        { refId: "media:0:1", x: 881, y: 11 },
        { refId: expectedVideoRef, x: 0, y: expectedY },
      ],
      manualEdges: [{ id: "legacy-media-edge", sourceRef: expectedVideoRef, targetRef: "frame:0" }],
    };
    const originalDocument = structuredClone(document);

    const graph = buildCanvasGraph(sourceChapter, [], [character], [], [], document);

    expect(graph.nodes.find((node) => node.ref === expectedVideoRef))
      .toMatchObject({ kind: "video", detail: videoUrl, x: 0, y: expectedY });
    expect(graph.edges.some((edge) => edge.id === "legacy-media-edge" && edge.manual)).toBe(true);
    if (preview === "/uploads/preview.png" || preview === "data:image/png;base64,AAAA") {
      expect(graph.nodes.find((node) => node.ref === "media:0:0"))
        .toMatchObject({ kind: "preview", detail: preview });
    }
    expect(sourceChapter).toEqual(originalChapter);
    expect(document).toEqual(originalDocument);
  });

  it("places all unpositioned previews and videos in a stable non-overlapping media lane", () => {
    const twoFrames = {
      ...chapter,
      content: [
        { ...chapter.content![0], preview: "/uploads/preview-a.png", videos: ["/uploads/a.mp4", "/uploads/b.mp4"] },
        { ...chapter.content![0], preview: "/uploads/preview-b.png", videos: ["/uploads/c.mp4"] },
      ],
    };
    const graph = buildCanvasGraph(twoFrames, [], [character], [], [], {
      nodeLayouts: [{ refId: "media:1:1", x: 0, y: 0 }],
    });
    const media = graph.nodes.filter((node) => node.kind === "preview" || node.kind === "video");

    expect(media.find((node) => node.ref === "media:1:1")?.x).toBe(0);
    expect(media.find((node) => node.ref === "media:1:1")?.y).toBe(0);
    expect(media).toHaveLength(5);
    expect(media.every((node, index) => media.slice(index + 1).every((other) =>
      node.x + node.width <= other.x || other.x + other.width <= node.x ||
      node.y + node.height <= other.y || other.y + other.height <= node.y,
    ))).toBe(true);
  });

  it("places new source columns without overlap while respecting explicit zero layouts", () => {
    const allSourcesChapter = {
      ...chapter,
      content: [{ ...chapter.content![0], character: ["character-a"], scene: ["scene-a"], prop: ["prop-a"], storyboard: ["storyboard-a"] }],
    };
    const scene = { id: "scene-a", series_id: "series-a", title: "屋内", description: "scene", image_url: null } as unknown as import("../../shared/api/contracts").Scene;
    const prop = { id: "prop-a", series_id: "series-a", name: "信件", description: "prop", image_url: null } as unknown as import("../../shared/api/contracts").Prop;
    const graph = buildCanvasGraph(allSourcesChapter, [storyboard], [character], [scene], [prop], {
      nodeLayouts: [{ refId: "character:character-a", x: 0, y: 0 }],
    });
    const nodes = graph.nodes.filter((node) => ["character", "scene", "prop", "frame", "media"].includes(node.kind));
    expect(nodes.find((node) => node.kind === "character")?.x).toBe(0);
    expect([...nodes].every((node, index) => nodes.slice(index + 1).every((other) =>
      node.x + node.width <= other.x || other.x + other.width <= node.x ||
      node.y + node.height <= other.y || other.y + other.height <= node.y,
    ))).toBe(true);
  });

  it("projects legacy frame layout separately from source and storyboard image refs", () => {
    const sourceVersion = "8be1499c4eb8b1a1f8e20d020cd52796e938726f0a1889cfecc07641d8d28a57";
    const sourceChapter = {
      ...chapter,
      content: [{ ...chapter.content![0], storyboard: ["storyboard-a"], videos: ["same.mp4", "same.mp4", "pending"] }],
    };
    const sourceStoryboard = { ...storyboard, frame_index: 0 };
    const graph = buildCanvasGraph(sourceChapter, [sourceStoryboard], [character], [], [], {
      nodeLayouts: [
        { refId: "frame:0", x: 900, y: 0 },
        { refId: "storyboard:storyboard-a", x: 950, y: 20 },
        { refId: "media:0:0", x: 980, y: 40 },
      ],
    }, sourceVersion);

    const frame = graph.nodes.find((node) => node.kind === "frame");
    const image = graph.nodes.find((node) => node.ref === "storyboard:storyboard-a");
    const preview = graph.nodes.find((node) => node.kind === "preview");
    const videos = graph.nodes.filter((node) => node.kind === "video");
    expect(frame).toMatchObject({
      ref: "frame:storyboard-a",
      sourceTarget: { index: 0, storyboard_id: "storyboard-a" },
      x: 900,
      y: 0,
      layoutRef: "frame:0",
    });
    expect(image?.ref).toBe("storyboard:storyboard-a");
    expect(preview?.ref).toBe(`media:${sourceVersion}:frame:storyboard-a:preview`);
    expect(videos.map((node) => node.sourceSlot)).toEqual([0, 1, 2]);
    expect(videos.map((node) => node.ref)).toEqual([
      `media:${sourceVersion}:frame:storyboard-a:video:0`,
      `media:${sourceVersion}:frame:storyboard-a:video:1`,
      `media:${sourceVersion}:frame:storyboard-a:video:2`,
    ]);
    expect(image?.x).toBe(950);
  });

  it("permits an explicit index target only for a truly unlinked frame and keeps ambiguous refs read-only", () => {
    const sourceVersion = "8be1499c4eb8b1a1f8e20d020cd52796e938726f0a1889cfecc07641d8d28a57";
    const noIdentity = { ...chapter, content: [{ text: "无分镜素材 ID", storyboard: [] }] };
    const graph = buildCanvasGraph(noIdentity, [], [], [], [], {}, sourceVersion);
    expect(graph.nodes.find((node) => node.kind === "frame"))
      .toMatchObject({ ref: `frame-index:${sourceVersion}:0`, sourceTarget: { index: 0, storyboard_id: null } });

    const duplicateIdentity = {
      ...chapter,
      content: [
        { text: "第一镜", storyboard: ["storyboard-a"] },
        { text: "第二镜", storyboard: ["storyboard-a"] },
      ],
    };
    const duplicateAssets = [
      { ...storyboard, frame_index: 0 },
      { ...storyboard, frame_index: 1 },
    ];
    const ambiguous = buildCanvasGraph(duplicateIdentity, duplicateAssets, [], [], [], {}, sourceVersion);
    expect(ambiguous.nodes.filter((node) => node.kind === "frame").every((node) => node.sourceTarget === null)).toBe(true);
  });
});
