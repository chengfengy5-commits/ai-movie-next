import type { CanvasDocument } from "./canvasDocument";

export interface CanvasHistory {
  undo: CanvasDocument[];
  redo: CanvasDocument[];
}

export const EMPTY_CANVAS_HISTORY: CanvasHistory = { undo: [], redo: [] };

export function pushCanvasHistory(
  history: CanvasHistory,
  current: CanvasDocument,
  limit = 50,
): CanvasHistory {
  return { undo: [...history.undo, current].slice(-limit), redo: [] };
}

export function undoCanvasHistory(
  history: CanvasHistory,
  current: CanvasDocument,
): { document: CanvasDocument; history: CanvasHistory } | null {
  const document = history.undo.at(-1);
  if (document === undefined) return null;
  return { document, history: { undo: history.undo.slice(0, -1), redo: [...history.redo, current].slice(-50) } };
}

export function redoCanvasHistory(
  history: CanvasHistory,
  current: CanvasDocument,
): { document: CanvasDocument; history: CanvasHistory } | null {
  const document = history.redo.at(-1);
  if (document === undefined) return null;
  return { document, history: { undo: [...history.undo, current].slice(-50), redo: history.redo.slice(0, -1) } };
}
