import { describe, expect, it } from "vitest";
import { EMPTY_CANVAS_HISTORY, pushCanvasHistory, redoCanvasHistory, undoCanvasHistory } from "./canvasHistory";

describe("canvas local history", () => {
  it("caps undo at 50, clears redo after a new edit, and restores stable IDs", () => {
    let history = EMPTY_CANVAS_HISTORY;
    let current = { notes: [{ id: "note-a", text: "0" }] };
    for (let index = 0; index < 55; index += 1) {
      history = pushCanvasHistory(history, current);
      current = { notes: [{ id: "note-a", text: String(index + 1) }] };
    }
    expect(history.undo).toHaveLength(50);
    const undone = undoCanvasHistory(history, current);
    expect(undone?.document.notes).toEqual([{ id: "note-a", text: "54" }]);
    const redone = redoCanvasHistory(undone!.history, undone!.document);
    expect(redone?.document.notes).toEqual([{ id: "note-a", text: "55" }]);
    const branched = pushCanvasHistory(redone!.history, redone!.document);
    expect(branched.redo).toEqual([]);
  });
});
