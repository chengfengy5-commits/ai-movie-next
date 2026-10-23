import { describe, expect, it } from "vitest";
import {
  addCanvasItem,
  canvasLayout,
  canvasViewport,
  parseCanvasDocument,
  patchCanvasItem,
  patchCanvasViewport,
  patchNodeLayout,
} from "./canvasDocument";

describe("canvas document compatibility patches", () => {
  it("keeps unknown fields and legacy graph data while changing one layout and note", () => {
    const source = {
      version: 1,
      viewport: { x: 0, y: -8, zoom: 1.25, vendor: "keep" },
      nodeLayouts: { "frame:0": { x: 0, y: 12, extra: null }, untouched: { x: -4, y: 2 } },
      notes: [{ id: "note-a", text: "old", x: 0, y: 0, unknown: { keep: true } }],
      cards: [{ id: "card-a", kind: "chapter-card", text: "kept" }],
      manualEdges: [{ id: "edge-a", sourceRef: "dangling", targetRef: "frame:0", relation: "custom" }],
      hiddenRelations: ["custom", "unknown"],
      nodes: [{ id: "unknown-node", type: "future" }],
      edges: [{ source: "missing", target: "also-missing", type: "future" }],
      unknownTopLevel: { keep: [0, null, false] },
    };
    const moved = patchNodeLayout(source, "frame:0", { x: 0, y: -2 });
    const edited = patchCanvasItem(moved, "notes", "note-a", { text: "new" });

    expect(edited.nodeLayouts).toEqual({
      "frame:0": { x: 0, y: -2, extra: null },
      untouched: { x: -4, y: 2 },
    });
    expect(edited.notes).toEqual([{ id: "note-a", text: "new", x: 0, y: 0, unknown: { keep: true } }]);
    expect(edited.cards).toEqual(source.cards);
    expect(edited.manualEdges).toEqual(source.manualEdges);
    expect(edited.hiddenRelations).toEqual(source.hiddenRelations);
    expect(edited.nodes).toEqual(source.nodes);
    expect(edited.edges).toEqual(source.edges);
    expect(edited.unknownTopLevel).toEqual(source.unknownTopLevel);
    expect(source.notes[0]?.text).toBe("old");
  });

  it("preserves numeric zero and null while updating the viewport", () => {
    const document = parseCanvasDocument({
      viewport: { x: 0, y: null, zoom: 0 },
      nodeLayouts: { "frame:0": { x: 0, y: 0 } },
      optional: null,
    });
    expect(document).not.toBeNull();
    expect(canvasViewport(document!)).toEqual({ x: 0, y: 0, zoom: 1 });
    expect(canvasLayout(document!, "frame:0")).toEqual({ x: 0, y: 0 });
    const updated = patchCanvasViewport(document!, { x: 0, y: 4, zoom: 1.5 });
    expect(updated.viewport).toEqual({ x: 0, y: 4, zoom: 1.5 });
    expect(updated.optional).toBeNull();
  });

  it("reads and precisely patches the legacy layout array by its persisted ref", () => {
    const source = {
      nodeLayouts: [
        { refId: "note-a", x: 0, y: -4, vendor: null },
        { refId: "unknown", x: 7, y: 8, extra: 0 },
        null,
        { refId: "note-a", x: 9, y: 10 },
      ],
      unknown: { keep: true },
    };
    expect(canvasLayout(source, "note-a")).toBeNull();
    expect(() => patchNodeLayout(source, "note-a", { x: 12, y: 13 })).toThrow("身份不唯一");

    const unique = { ...source, nodeLayouts: source.nodeLayouts.slice(0, 3) };
    expect(canvasLayout(unique, "note-a")).toEqual({ x: 0, y: -4 });
    const changed = patchNodeLayout(unique, "note-a", { x: 0, y: 20 });
    expect(changed.nodeLayouts).toEqual([
      { refId: "note-a", x: 0, y: 20, vendor: null },
      { refId: "unknown", x: 7, y: 8, extra: 0 },
      null,
    ]);
  });

  it("writes the legacy layout array when the first layout is added to a document", () => {
    const source = {
      notes: [],
      unknown: { preserve: [0, null] },
      viewport: { x: 0, y: 0, zoom: 1 },
    };

    const changed = patchNodeLayout(source, "frame:0", { x: 0, y: 24 });

    expect(changed.nodeLayouts).toEqual([{ refId: "frame:0", x: 0, y: 24 }]);
    expect(changed.unknown).toEqual(source.unknown);
    expect(changed.viewport).toEqual(source.viewport);
    expect(source).not.toHaveProperty("nodeLayouts");
  });

  it("rejects ambiguous item identities and non-object documents", () => {
    const document = { notes: [{ id: "same", text: "a" }, { id: "same", text: "b" }] };
    expect(() => patchCanvasItem(document, "notes", "same", { text: "overwrite" }))
      .toThrow("身份不唯一");
    expect(() => addCanvasItem({ notes: [{ id: "same" }] }, "notes", { id: "same" }))
      .toThrow("身份重复");
    expect(parseCanvasDocument(null)).toBeNull();
    expect(parseCanvasDocument([])).toBeNull();
  });
});
