import type { WorkspaceServices } from "../../shared/api/services";

export type WorkbenchResourceCategory = "storyboard" | "characters" | "scenes" | "props" | "fused";

export type WorkbenchSourceTarget =
  | { readonly kind: "chapter"; readonly chapterId: string }
  | { readonly kind: "frame"; readonly chapterId: string; readonly storyboardAssetId: string }
  | {
    readonly kind: "asset";
    readonly chapterId: string;
    readonly category: WorkbenchResourceCategory;
    readonly assetId: string;
  };

export interface WorkbenchSourceSelection {
  readonly seriesId: string;
  readonly chapterId: string;
  readonly target: WorkbenchSourceTarget;
  isCurrent(): boolean;
}

export interface WorkbenchSourceIntent extends WorkbenchSourceSelection {
  readonly actorId: string;
  readonly apiBaseUrl: string | null;
  readonly services: WorkspaceServices;
  readonly sessionGeneration: number;
  readonly navigationEpoch: number;
}
