import { isRecord } from "../../shared/api/canvasData";

export interface CanvasPoint {
  x: number;
  y: number;
}

export interface CanvasViewport extends CanvasPoint {
  zoom: number;
}

export interface CanvasNote {
  id: string;
  text: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CanvasDocument extends Record<string, unknown> {}

export function parseCanvasDocument(value: unknown): CanvasDocument | null {
  return isRecord(value) ? value : null;
}

function copyDocument(document: CanvasDocument): CanvasDocument {
  return JSON.parse(JSON.stringify(document)) as CanvasDocument;
}

function ensureRecordField(document: CanvasDocument, key: string): Record<string, unknown> {
  const value = document[key];
  if (value === undefined) {
    document[key] = {};
    return document[key] as Record<string, unknown>;
  }
  if (!isRecord(value)) throw new Error("画布文档结构无法安全编辑。");
  return value;
}

function ensureArrayField(document: CanvasDocument, key: string): unknown[] {
  const value = document[key];
  if (value === undefined) {
    document[key] = [];
    return document[key] as unknown[];
  }
  if (!Array.isArray(value)) throw new Error("画布文档结构无法安全编辑。");
  return value;
}

export function canvasViewport(document: CanvasDocument): CanvasViewport {
  const value = document.viewport;
  if (!isRecord(value)) return { x: 0, y: 0, zoom: 1 };
  return {
    x: typeof value.x === "number" && Number.isFinite(value.x) ? value.x : 0,
    y: typeof value.y === "number" && Number.isFinite(value.y) ? value.y : 0,
    zoom: typeof value.zoom === "number" && Number.isFinite(value.zoom) && value.zoom > 0 ? value.zoom : 1,
  };
}

export function canvasLayout(document: CanvasDocument, ref: string): CanvasPoint | null {
  const layouts = document.nodeLayouts;
  let layout: unknown;
  if (Array.isArray(layouts)) {
    const matches = layouts.filter((item) => isRecord(item) && item.refId === ref);
    if (matches.length !== 1) return null;
    layout = matches[0];
  } else if (isRecord(layouts)) {
    layout = layouts[ref];
  } else {
    return null;
  }
  if (!isRecord(layout) || typeof layout.x !== "number" || typeof layout.y !== "number"
    || !Number.isFinite(layout.x) || !Number.isFinite(layout.y)) return null;
  return { x: layout.x, y: layout.y };
}

export function hasCanvasLayout(document: CanvasDocument, ref: string): boolean {
  const layouts = document.nodeLayouts;
  if (Array.isArray(layouts)) return layouts.filter((item) => isRecord(item) && item.refId === ref).length === 1;
  return isRecord(layouts) && Object.prototype.hasOwnProperty.call(layouts, ref);
}

export function patchNodeLayout(document: CanvasDocument, ref: string, point: CanvasPoint): CanvasDocument {
  const next = copyDocument(document);
  if (next.nodeLayouts === undefined) {
    next.nodeLayouts = [{ refId: ref, x: point.x, y: point.y }];
    return next;
  }
  if (Array.isArray(next.nodeLayouts)) {
    const layouts = next.nodeLayouts;
    const indexes = layouts.flatMap((item, index) => isRecord(item) && item.refId === ref ? [index] : []);
    if (indexes.length > 1) throw new Error("画布布局身份不唯一，已停用此项编辑。");
    if (indexes.length === 1) {
      const index = indexes[0]!;
      layouts[index] = { ...(layouts[index] as Record<string, unknown>), x: point.x, y: point.y };
    } else {
      layouts.push({ refId: ref, x: point.x, y: point.y });
    }
    return next;
  }
  const layouts = ensureRecordField(next, "nodeLayouts");
  const previous = layouts[ref];
  layouts[ref] = isRecord(previous) ? { ...previous, x: point.x, y: point.y } : { x: point.x, y: point.y };
  return next;
}

export function patchCanvasViewport(document: CanvasDocument, viewport: CanvasViewport): CanvasDocument {
  const next = copyDocument(document);
  const previous = isRecord(next.viewport) ? next.viewport : {};
  next.viewport = { ...previous, x: viewport.x, y: viewport.y, zoom: viewport.zoom };
  return next;
}

function uniqueItemIndex(items: unknown[], id: string): number {
  const indexes = items.flatMap((item, index) => isRecord(item) && item.id === id ? [index] : []);
  return indexes.length === 1 ? indexes[0]! : -1;
}

function patchUniqueItem(
  document: CanvasDocument,
  collection: "notes" | "cards" | "manualEdges",
  id: string,
  update: Record<string, unknown> | null,
): CanvasDocument {
  const next = copyDocument(document);
  const items = ensureArrayField(next, collection);
  const index = uniqueItemIndex(items, id);
  if (index < 0) throw new Error("画布项目身份不唯一，已停用此项编辑。");
  if (update === null) items.splice(index, 1);
  else items[index] = { ...(items[index] as Record<string, unknown>), ...update };
  return next;
}

export function addCanvasItem(
  document: CanvasDocument,
  collection: "notes" | "cards" | "manualEdges",
  item: Record<string, unknown>,
): CanvasDocument {
  const next = copyDocument(document);
  const items = ensureArrayField(next, collection);
  if (typeof item.id !== "string" || uniqueItemIndex(items, item.id) !== -1) {
    throw new Error("画布项目身份重复，未创建项目。");
  }
  items.push(item);
  return next;
}

export function patchCanvasItem(
  document: CanvasDocument,
  collection: "notes" | "cards" | "manualEdges",
  id: string,
  update: Record<string, unknown>,
): CanvasDocument {
  return patchUniqueItem(document, collection, id, update);
}

export function removeCanvasItem(
  document: CanvasDocument,
  collection: "notes" | "cards" | "manualEdges",
  id: string,
): CanvasDocument {
  return patchUniqueItem(document, collection, id, null);
}

export function patchHiddenRelation(document: CanvasDocument, relation: string, hidden: boolean): CanvasDocument {
  const next = copyDocument(document);
  const value = next.hiddenRelations;
  if (value !== undefined && !Array.isArray(value)) throw new Error("画布文档结构无法安全编辑。");
  const relations = (value ?? []) as unknown[];
  const matches = relations.filter((item) => item === relation).length;
  if (matches > 1) throw new Error("关系身份重复，未修改显示状态。");
  if (hidden && matches === 0) relations.push(relation);
  if (!hidden && matches === 1) relations.splice(relations.indexOf(relation), 1);
  next.hiddenRelations = relations;
  return next;
}

export function documentEqual(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}
