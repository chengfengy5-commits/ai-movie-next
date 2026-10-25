import { isRecord } from "../../shared/api/canvasData";
import {
  resolveCanvasFrameTarget,
  type CanvasCreationReceipt,
  type CanvasSourceCommand,
  type CanvasSourceSnapshot,
} from "../../shared/api/canvasSources";
import type { CanvasDocument } from "./canvasDocument";

export interface CanvasSourceTransition {
  command: CanvasSourceCommand;
  receipt: CanvasCreationReceipt | null;
}

interface CanvasSourceDocumentBinding {
  ref: string;
  seriesId: string;
  chapterId: string;
  sourceVersion: string;
  sourceRef: string;
  [key: string]: unknown;
}

const SOURCE_BINDINGS_FIELD = "canvasSourceBindings";

function sameJsonValue(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length
      && left.every((value, index) => sameJsonValue(value, right[index]));
  }
  if (isRecord(left) || isRecord(right)) {
    if (!isRecord(left) || !isRecord(right)) return false;
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return leftKeys.length === rightKeys.length
      && leftKeys.every((key, index) => key === rightKeys[index] && sameJsonValue(left[key], right[key]));
  }
  return false;
}

function frames(snapshot: CanvasSourceSnapshot): unknown[] {
  return Array.isArray(snapshot.chapter.content) ? snapshot.chapter.content : [];
}

function frameRef(snapshot: CanvasSourceSnapshot, index: number): string | null {
  const target = resolveCanvasFrameTarget(snapshot.chapter, snapshot.storyboard_assets, index);
  if (target === null) return null;
  return target.storyboard_id === null
    ? `frame-index:${snapshot.version}:${index}`
    : `frame:${target.storyboard_id}`;
}

function isExactTarget(snapshot: CanvasSourceSnapshot, index: number, target: { index: number; storyboard_id: string | null }): boolean {
  if (index !== target.index) return false;
  const actual = resolveCanvasFrameTarget(snapshot.chapter, snapshot.storyboard_assets, index);
  return actual !== null && actual.storyboard_id === target.storyboard_id;
}

function insertionIndex(
  snapshot: CanvasSourceSnapshot,
  after: CanvasSourceSnapshot,
  command: CanvasSourceCommand,
  receipt: CanvasCreationReceipt | null,
): number | null {
  if (command.type === "frame.insert") {
    const expected = command.after === null ? 0 : command.after.index + 1;
    if (command.after !== null && !isExactTarget(snapshot, command.after.index, command.after)) return null;
    const id = receipt?.target.id;
    const insertedTarget = resolveCanvasFrameTarget(after.chapter, after.storyboard_assets, expected);
    if (receipt?.kind !== "frame" || receipt.target.index !== expected
      || typeof id !== "string" || receipt.target.storyboard_id !== id
      || insertedTarget?.storyboard_id !== id
      || snapshot.storyboard_assets.some((asset) => asset.id === id)
      || frames(snapshot).some((frame) => isRecord(frame) && Array.isArray(frame.storyboard) && frame.storyboard.includes(id))) {
      return null;
    }
    return expected;
  }
  if (command.type === "creation.undo" && receipt?.kind === "frame") {
    const index = receipt.target.index;
    const id = receipt.target.storyboard_id;
    if (typeof index !== "number" || !Number.isSafeInteger(index) || index < 0
      || (typeof id !== "string" && id !== null)) return null;
    return isExactTarget(snapshot, index, { index, storyboard_id: id as string | null }) ? index : null;
  }
  if (command.type === "frame.delete") {
    return isExactTarget(snapshot, command.target.index, command.target) ? command.target.index : null;
  }
  return null;
}

function expectedFrameAfter(
  baseline: CanvasSourceSnapshot,
  before: unknown,
  command: CanvasSourceCommand,
  transition: CanvasSourceTransition,
  oldIndex: number,
): unknown | null {
  if (!isRecord(before)) return null;
  const after = { ...before };
  if (command.type === "frame.update" && isExactTarget(baseline, oldIndex, command.target)) {
    after.text = command.text;
    return after;
  }
  if (command.type === "media.update" && isExactTarget(baseline, oldIndex, command.target)) {
    if (command.field === "preview" && command.slot === null) {
      after.preview = command.value;
      return after;
    }
    if (command.field === "video" && typeof command.slot === "number" && Array.isArray(before.videos)
      && command.slot >= 0 && command.slot < before.videos.length) {
      const videos = [...before.videos];
      videos[command.slot] = command.value;
      after.videos = videos;
      return after;
    }
    return null;
  }
  if (command.type === "media.append" && isExactTarget(baseline, oldIndex, command.target)) {
    const videos = Array.isArray(before.videos) ? [...before.videos] : [];
    after.videos = [...videos, command.value];
    return after;
  }
  if (command.type === "creation.undo" && transition.receipt?.kind === "video") {
    const target = transition.receipt.target;
    const index = target.index;
    const slot = target.slot;
    const storyboardId = target.storyboard_id;
    if (index === oldIndex && typeof index === "number" && typeof slot === "number"
      && Number.isSafeInteger(slot) && slot >= 0 && (typeof storyboardId === "string" || storyboardId === null)
      && isExactTarget(baseline, oldIndex, { index, storyboard_id: storyboardId })
      && Array.isArray(before.videos) && slot < before.videos.length) {
      after.videos = before.videos.filter((_value, videoSlot) => videoSlot !== slot);
      return after;
    }
    return null;
  }
  return after;
}

function commandFrameChangedAt(
  before: CanvasSourceSnapshot,
  after: CanvasSourceSnapshot,
  command: CanvasSourceCommand,
  transition: CanvasSourceTransition,
  oldIndex: number,
  newIndex: number,
): boolean {
  const previous = frames(before)[oldIndex];
  const current = frames(after)[newIndex];
  if (!isRecord(previous)) return false;
  const expected = expectedFrameAfter(before, previous, command, transition, oldIndex);
  return expected !== null && sameJsonValue(expected, current);
}

function mappedFrameIndexes(
  before: CanvasSourceSnapshot,
  after: CanvasSourceSnapshot,
  transition: CanvasSourceTransition,
): Map<number, number> {
  if (before.chapter_id !== after.chapter_id || before.series_id !== after.series_id
    || before.version === after.version) return new Map();
  const { command, receipt } = transition;
  let removedIndex: number | null = null;
  let insertedIndex: number | null = null;

  if (command.type === "frame.insert") {
    insertedIndex = insertionIndex(before, after, command, receipt);
    if (insertedIndex === null) return new Map();
  } else if (command.type === "frame.delete") {
    if (!isExactTarget(before, command.target.index, command.target)) return new Map();
    removedIndex = command.target.index;
  } else if (command.type === "creation.undo" && receipt?.kind === "frame") {
    removedIndex = insertionIndex(before, after, command, receipt);
    if (removedIndex === null) return new Map();
  }

  const beforeFrames = frames(before);
  const afterFrames = frames(after);
  if (insertedIndex !== null && afterFrames.length !== beforeFrames.length + 1) return new Map();
  if (removedIndex !== null && afterFrames.length !== beforeFrames.length - 1) return new Map();
  if (insertedIndex === null && removedIndex === null && afterFrames.length !== beforeFrames.length) return new Map();

  const mappings = new Map<number, number>();
  beforeFrames.forEach((_frame, oldIndex) => {
    if (removedIndex === oldIndex) return;
    const newIndex = insertedIndex !== null && oldIndex >= insertedIndex
      ? oldIndex + 1
      : removedIndex !== null && oldIndex > removedIndex ? oldIndex - 1 : oldIndex;
    if (newIndex >= afterFrames.length) return;
    const oldRef = frameRef(before, oldIndex);
    const newRef = frameRef(after, newIndex);
    if (oldRef === null || newRef === null) return;
    const oldTarget = resolveCanvasFrameTarget(before.chapter, before.storyboard_assets, oldIndex);
    const newTarget = resolveCanvasFrameTarget(after.chapter, after.storyboard_assets, newIndex);
    if (oldTarget === null || newTarget === null || oldTarget.storyboard_id !== newTarget.storyboard_id) return;
    if (!commandFrameChangedAt(before, after, command, transition, oldIndex, newIndex)) return;
    mappings.set(oldIndex, newIndex);
  });
  return mappings;
}

function addLegacySourceRefAlias(
  aliases: Map<string, { beforeSourceRef: string; afterFrameRef?: string } | null>,
  legacyRef: string,
  beforeSourceRef: string,
  afterFrameRef?: string,
): void {
  if (legacyRef === beforeSourceRef) return;
  const existing = aliases.get(legacyRef);
  const target = { beforeSourceRef, ...(afterFrameRef === undefined ? {} : { afterFrameRef }) };
  aliases.set(legacyRef, existing === undefined
    || existing?.beforeSourceRef === beforeSourceRef && existing.afterFrameRef === afterFrameRef
    ? target
    : null);
}

function legacySourceRefAliases(
  before: CanvasSourceSnapshot,
  after: CanvasSourceSnapshot,
  mappedIndexes: Map<number, number>,
): Map<string, { beforeSourceRef: string; afterFrameRef?: string } | null> {
  const aliases = new Map<string, { beforeSourceRef: string; afterFrameRef?: string } | null>();
  const beforeFrames = frames(before);
  const beforeFrameRefs = beforeFrames.map((_frame, index) => frameRef(before, index));
  for (const [oldIndex, newIndex] of mappedIndexes) {
    const oldFrameRef = beforeFrameRefs[oldIndex];
    const afterFrameRef = frameRef(after, newIndex);
    const frame = beforeFrames[oldIndex];
    if (oldFrameRef === null || oldFrameRef === undefined || afterFrameRef === null || !isRecord(frame)) continue;

    const legacyFrameRef = `frame:${oldIndex}`;
    const frameAliasConflicts = beforeFrameRefs.some((sourceRef, index) => index !== oldIndex
      && sourceRef === legacyFrameRef);
    if (!frameAliasConflicts) addLegacySourceRefAlias(aliases, legacyFrameRef, oldFrameRef, afterFrameRef);

    const preview = frame.preview;
    const previewReservesOrdinal = Boolean(preview);
    if (typeof preview === "string" && preview.trim() !== "") {
      addLegacySourceRefAlias(
        aliases,
        `media:${oldIndex}:0`,
        `media:${before.version}:${oldFrameRef}:preview`,
      );
    }

    let videoOrdinal = previewReservesOrdinal ? 1 : 0;
    const videos = Array.isArray(frame.videos) ? frame.videos : [];
    videos.forEach((video, slot) => {
      const ordinal = videoOrdinal;
      if (typeof video === "string" && video.trim() !== "") {
        addLegacySourceRefAlias(
          aliases,
          `media:${oldIndex}:${ordinal}`,
          `media:${before.version}:${oldFrameRef}:video:${slot}`,
        );
      }
      if (typeof video === "string") videoOrdinal += 1;
    });
  }
  return aliases;
}

function previewWasProven(
  before: CanvasSourceSnapshot,
  after: CanvasSourceSnapshot,
  transition: CanvasSourceTransition,
  oldIndex: number,
  newIndex: number,
): boolean {
  const oldFrame = frames(before)[oldIndex];
  const newFrame = frames(after)[newIndex];
  if (!isRecord(oldFrame) || !isRecord(newFrame)) return false;
  const oldPreview = oldFrame.preview;
  const newPreview = newFrame.preview;
  if (typeof oldPreview !== "string" || oldPreview.trim() === ""
    || typeof newPreview !== "string" || newPreview.trim() === "") return false;
  const command = transition.command;
  if (command.type === "media.update" && command.field === "preview"
    && command.target.index === oldIndex && command.slot === null) {
    return isExactTarget(before, oldIndex, command.target) && newPreview === command.value;
  }
  return sameJsonValue(oldPreview, newPreview);
}

function videoSlotMap(
  before: CanvasSourceSnapshot,
  after: CanvasSourceSnapshot,
  transition: CanvasSourceTransition,
  oldIndex: number,
  newIndex: number,
): Map<number, number> {
  const oldFrame = frames(before)[oldIndex];
  const newFrame = frames(after)[newIndex];
  if (!isRecord(oldFrame) || !isRecord(newFrame)) return new Map();
  const oldVideos = Array.isArray(oldFrame.videos) ? oldFrame.videos : [];
  const newVideos = Array.isArray(newFrame.videos) ? newFrame.videos : [];
  const mapping = new Map<number, number>();
  const command = transition.command;

  if (sameJsonValue(oldVideos, newVideos)) {
    oldVideos.forEach((_value, slot) => mapping.set(slot, slot));
    return mapping;
  }
  if (command.type === "media.append" && command.target.index === oldIndex
    && isExactTarget(before, oldIndex, command.target)
    && transition.receipt?.kind === "video"
    && transition.receipt.target.index === oldIndex
    && transition.receipt.target.storyboard_id === command.target.storyboard_id
    && transition.receipt.target.slot === oldVideos.length
    && transition.receipt.target.value === command.value
    && newVideos.length === oldVideos.length + 1
    && sameJsonValue(newVideos.slice(0, oldVideos.length), oldVideos)
    && newVideos.at(-1) === command.value) {
    oldVideos.forEach((_value, slot) => mapping.set(slot, slot));
    return mapping;
  }
  if (command.type === "media.update" && command.field === "video" && command.target.index === oldIndex
    && command.slot !== null && isExactTarget(before, oldIndex, command.target)
    && oldVideos.length === newVideos.length && command.slot >= 0 && command.slot < oldVideos.length
    && newVideos[command.slot] === command.value) {
    const expected = [...oldVideos];
    expected[command.slot] = command.value;
    if (sameJsonValue(expected, newVideos)) oldVideos.forEach((_value, slot) => mapping.set(slot, slot));
    return mapping;
  }
  if (command.type === "creation.undo" && transition.receipt?.kind === "video") {
    const target = transition.receipt.target;
    const removedSlot = target.slot;
    if (target.index !== oldIndex || typeof removedSlot !== "number" || !Number.isSafeInteger(removedSlot)
      || removedSlot < 0 || removedSlot >= oldVideos.length || newVideos.length !== oldVideos.length - 1
      || removedSlot !== oldVideos.length - 1 || target.value !== oldVideos[removedSlot]
      || !isExactTarget(before, oldIndex, {
        index: oldIndex,
        storyboard_id: typeof target.storyboard_id === "string" ? target.storyboard_id : null,
      })
      || !sameJsonValue(oldVideos.filter((_value, slot) => slot !== removedSlot), newVideos)) return mapping;
    oldVideos.forEach((_value, slot) => {
      if (slot !== removedSlot) mapping.set(slot, slot < removedSlot ? slot : slot - 1);
    });
  }
  return mapping;
}

function setIfUnambiguous(map: Map<string, string>, from: string, to: string): void {
  if (from !== to && !map.has(from) && !Array.from(map.values()).includes(to)) map.set(from, to);
}

export function provenCanvasSourceRefMap(
  before: CanvasSourceSnapshot,
  after: CanvasSourceSnapshot,
  transition: CanvasSourceTransition,
): Map<string, string> {
  const frameIndexes = mappedFrameIndexes(before, after, transition);
  const refs = new Map<string, string>();
  for (const [oldIndex, newIndex] of frameIndexes) {
    const oldFrameRef = frameRef(before, oldIndex);
    const newFrameRef = frameRef(after, newIndex);
    if (oldFrameRef === null || newFrameRef === null) continue;
    setIfUnambiguous(refs, oldFrameRef, newFrameRef);

    if (previewWasProven(before, after, transition, oldIndex, newIndex)) {
      setIfUnambiguous(refs,
        `media:${before.version}:${oldFrameRef}:preview`,
        `media:${after.version}:${newFrameRef}:preview`);
    }
    for (const [oldSlot, newSlot] of videoSlotMap(before, after, transition, oldIndex, newIndex)) {
      if (typeof frames(before)[oldIndex] === "undefined" || typeof frames(after)[newIndex] === "undefined") continue;
      const oldVideos = isRecord(frames(before)[oldIndex]) && Array.isArray((frames(before)[oldIndex] as Record<string, unknown>).videos)
        ? (frames(before)[oldIndex] as Record<string, unknown>).videos as unknown[] : [];
      const newVideos = isRecord(frames(after)[newIndex]) && Array.isArray((frames(after)[newIndex] as Record<string, unknown>).videos)
        ? (frames(after)[newIndex] as Record<string, unknown>).videos as unknown[] : [];
      if (typeof oldVideos[oldSlot] !== "string" || typeof newVideos[newSlot] !== "string") continue;
      setIfUnambiguous(refs,
        `media:${before.version}:${oldFrameRef}:video:${oldSlot}`,
        `media:${after.version}:${newFrameRef}:video:${newSlot}`);
    }
  }
  return refs;
}

export function remapCanvasDocumentSourceRefs(
  document: CanvasDocument,
  refs: Map<string, string>,
): CanvasDocument {
  if (refs.size === 0) return document;
  const next = JSON.parse(JSON.stringify(document)) as CanvasDocument;
  const layouts = next.nodeLayouts;
  if (Array.isArray(layouts)) {
    const oldCounts = new Map<string, number>();
    const existing = new Set<string>();
    for (const item of layouts) {
      if (isRecord(item) && typeof item.refId === "string") {
        existing.add(item.refId);
        oldCounts.set(item.refId, (oldCounts.get(item.refId) ?? 0) + 1);
      }
    }
    for (const item of layouts) {
      if (!isRecord(item) || typeof item.refId !== "string") continue;
      const destination = refs.get(item.refId);
      if (destination !== undefined && oldCounts.get(item.refId) === 1 && !existing.has(destination)) {
        item.refId = destination;
        existing.add(destination);
      }
    }
  } else if (isRecord(layouts)) {
    const moves = Object.entries(layouts).flatMap(([ref, layout]) => {
      const destination = refs.get(ref);
      return destination !== undefined && !Object.prototype.hasOwnProperty.call(layouts, destination)
        ? [[ref, destination, layout] as const]
        : [];
    });
    for (const [from] of moves) delete layouts[from];
    for (const [, to, layout] of moves) layouts[to] = layout;
  }

  if (Array.isArray(next.manualEdges)) {
    next.manualEdges = next.manualEdges.map((edge) => {
      if (!isRecord(edge)) return edge;
      const sourceRef = typeof edge.sourceRef === "string" ? refs.get(edge.sourceRef) : undefined;
      const targetRef = typeof edge.targetRef === "string" ? refs.get(edge.targetRef) : undefined;
      return sourceRef === undefined && targetRef === undefined
        ? edge
        : { ...edge, ...(sourceRef === undefined ? {} : { sourceRef }), ...(targetRef === undefined ? {} : { targetRef }) };
    });
  }
  return next;
}

function parseSourceBinding(value: unknown): CanvasSourceDocumentBinding | null {
  if (!isRecord(value)
    || typeof value.ref !== "string" || value.ref.trim() === ""
    || typeof value.seriesId !== "string" || value.seriesId.trim() === ""
    || typeof value.chapterId !== "string" || value.chapterId.trim() === ""
    || typeof value.sourceVersion !== "string" || value.sourceVersion.trim() === ""
    || typeof value.sourceRef !== "string" || value.sourceRef.trim() === "") return null;
  return value as CanvasSourceDocumentBinding;
}

function stableBindingRef(sourceRef: string): string {
  return `canvas-source-binding:${encodeURIComponent(sourceRef)}`;
}

function documentUsesRef(document: CanvasDocument, ref: string): boolean {
  const layouts = document.nodeLayouts;
  if (Array.isArray(layouts) && layouts.some((layout) => isRecord(layout) && layout.refId === ref)) return true;
  if (isRecord(layouts) && Object.prototype.hasOwnProperty.call(layouts, ref)) return true;
  const edges = document.manualEdges;
  return Array.isArray(edges) && edges.some((edge) => isRecord(edge)
    && (edge.sourceRef === ref || edge.targetRef === ref));
}

export function resolveCanvasSourceBindingRef(
  document: CanvasDocument,
  scope: Pick<CanvasSourceSnapshot, "series_id" | "chapter_id" | "version">,
  sourceRef: string,
): string {
  const value = document[SOURCE_BINDINGS_FIELD];
  if (!Array.isArray(value)) return sourceRef;
  const bindings = value.map(parseSourceBinding).filter((binding): binding is CanvasSourceDocumentBinding => binding !== null);
  const matches = bindings.filter((binding) => binding.seriesId === scope.series_id
    && binding.chapterId === scope.chapter_id && binding.sourceVersion === scope.version
    && binding.sourceRef === sourceRef);
  if (matches.length !== 1) return sourceRef;
  const refCount = bindings.filter((binding) => binding.ref === matches[0]!.ref).length;
  return refCount === 1 ? matches[0]!.ref : sourceRef;
}

export function bindCanvasSourceDocumentTransition(
  document: CanvasDocument,
  before: CanvasSourceSnapshot,
  after: CanvasSourceSnapshot,
  transition: CanvasSourceTransition,
): { document: CanvasDocument; refs: Map<string, string> } {
  const provenRefs = provenCanvasSourceRefMap(before, after, transition);
  const frameIndexes = mappedFrameIndexes(before, after, transition);
  if (provenRefs.size === 0 && frameIndexes.size === 0) return { document, refs: provenRefs };
  const currentValue = document[SOURCE_BINDINGS_FIELD];
  if (currentValue !== undefined && !Array.isArray(currentValue)) return { document, refs: new Map() };

  const next = JSON.parse(JSON.stringify(document)) as CanvasDocument;
  const rows = Array.isArray(next[SOURCE_BINDINGS_FIELD]) ? next[SOURCE_BINDINGS_FIELD] as unknown[] : [];
  const bindings = rows.map(parseSourceBinding);
  const refs = new Map<string, string>();
  let changed = false;

  for (const [sourceRef, nextSourceRef] of provenRefs) {
    const matches = bindings.flatMap((binding, index) => binding !== null
      && binding.seriesId === before.series_id && binding.chapterId === before.chapter_id
      && binding.sourceVersion === before.version && binding.sourceRef === sourceRef
      ? [index] : []);
    if (matches.length > 1) continue;

    const existingBindingIndex = matches[0];
    const existingBinding = existingBindingIndex === undefined ? null : bindings[existingBindingIndex]!;
    const ref = existingBinding?.ref ?? stableBindingRef(sourceRef);
    if (existingBinding === null && documentUsesRef(document, ref)) continue;
    const refConflict = bindings.some((binding, index) => binding !== null && binding.ref === ref
      && index !== existingBindingIndex);
    const destinationConflict = bindings.some((binding, index) => binding !== null
      && binding.seriesId === after.series_id && binding.chapterId === after.chapter_id
      && binding.sourceVersion === after.version && binding.sourceRef === nextSourceRef
      && index !== existingBindingIndex);
    if (refConflict || destinationConflict) continue;

    const updated: CanvasSourceDocumentBinding = {
      ...(existingBinding ?? {}),
      ref,
      seriesId: after.series_id,
      chapterId: after.chapter_id,
      sourceVersion: after.version,
      sourceRef: nextSourceRef,
    };
    if (existingBindingIndex === undefined) {
      rows.push(updated);
      bindings.push(updated);
    } else {
      rows[existingBindingIndex] = updated;
      bindings[existingBindingIndex] = updated;
    }
    refs.set(sourceRef, ref);
    changed = true;
  }

  if (changed) next[SOURCE_BINDINGS_FIELD] = rows;
  const remapped = changed ? remapCanvasDocumentSourceRefs(next, refs) : next;
  const legacyRefs = new Map<string, string>();
  const aliases = legacySourceRefAliases(before, after, frameIndexes);
  const bindingsValue = next[SOURCE_BINDINGS_FIELD];
  const updatedBindings = Array.isArray(bindingsValue)
    ? bindingsValue.map(parseSourceBinding).filter((binding): binding is CanvasSourceDocumentBinding => binding !== null)
    : [];
  for (const [legacyRef, alias] of aliases) {
    if (alias === null || legacyRef === alias.beforeSourceRef || !documentUsesRef(document, legacyRef)) continue;
    const bindingRef = refs.get(alias.beforeSourceRef)
      ?? (alias.afterFrameRef === undefined ? undefined : resolveCanvasSourceBindingRef(next, after, alias.afterFrameRef));
    if (bindingRef === undefined) continue;
    const conflictingBinding = updatedBindings.some((binding) => binding.ref === legacyRef
      && !(binding.seriesId === after.series_id && binding.chapterId === after.chapter_id
        && binding.sourceVersion === after.version && binding.sourceRef === alias.afterFrameRef));
    if (!conflictingBinding) legacyRefs.set(legacyRef, bindingRef);
  }
  if (!changed && legacyRefs.size === 0) return { document, refs };
  return { document: remapCanvasDocumentSourceRefs(remapped, legacyRefs), refs };
}
