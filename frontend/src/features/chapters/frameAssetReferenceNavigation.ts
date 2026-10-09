import type { StoryboardFrame } from "../../shared/api/contracts";
import type {
  FrameAssetDirectory,
  FrameAssetReferenceCategory,
  FrameAssetReferencesProjection,
} from "./frameAssetReferences";

function referenceList(frame: StoryboardFrame, category: FrameAssetReferenceCategory): unknown {
  switch (category) {
    case "characters":
      return frame.character;
    case "scenes":
      return frame.scene;
    case "props":
      return frame.prop;
  }
}

/** Resolves a visible projection row back to its raw category ID at the same source index. */
export function resolveFrameAssetReferenceNavigationId(
  frame: StoryboardFrame,
  category: FrameAssetReferenceCategory,
  seriesId: string,
  directory: FrameAssetDirectory,
  projection: FrameAssetReferencesProjection,
  rowIndex: number,
): string | null {
  if (
    directory.category !== category
    || projection.status !== "references"
  ) {
    return null;
  }

  const references = referenceList(frame, category);
  if (!Array.isArray(references) || references.length !== projection.rows.length) {
    return null;
  }

  const row = projection.rows[rowIndex];
  const rawId: unknown = references[rowIndex];
  if (row?.status !== "resolved" || typeof rawId !== "string" || rawId.trim() === "") {
    return null;
  }

  const matches = directory.items.filter((asset) => asset.id === rawId);
  if (matches.length !== 1 || matches[0]?.series_id !== seriesId) {
    return null;
  }
  return rawId;
}
