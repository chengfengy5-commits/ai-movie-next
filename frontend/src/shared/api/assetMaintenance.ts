import { InvalidResponseError } from "./contracts";
import type { AssetLibraryType } from "./contracts";

export type AssetMaintenanceType = "character" | "scene" | "prop";

export interface AssetOrphanCandidate {
  type: AssetMaintenanceType;
  id: string;
}

export interface AssetOrphanCheckInput {
  items: AssetOrphanCandidate[];
  exclude_chapter_id?: string | null;
}

export interface AssetOrphanCheckResponse {
  orphans: AssetOrphanCandidate[];
}

export interface AssetDuplicateItem {
  id: string;
  name: string;
  description: string;
  image_url: string | null;
  ref_count: number;
  aliases: string[];
  canonical_key: string | null;
}

export interface AssetDuplicateGroup {
  index: number;
  items: AssetDuplicateItem[];
}

export interface AssetDuplicateList {
  type: AssetMaintenanceType;
  total: number;
  ignored_total: number;
  groups: AssetDuplicateGroup[];
}

export interface AssetIgnoredDuplicate {
  id: string;
  asset_id_a: string;
  asset_id_b: string;
  name_a: string;
  name_b: string;
  image_a: string | null;
  image_b: string | null;
  created_at: string | null;
}

export interface AssetIgnoredDuplicateList {
  type: AssetMaintenanceType;
  total: number;
  items: AssetIgnoredDuplicate[];
}

export interface SetAssetDuplicateIgnoredInput {
  type: AssetMaintenanceType;
  ids: string[];
  ignored: boolean;
}

export type SetAssetDuplicateIgnoredReceipt =
  | { ignored: true; pairs_added: number; pairs_total: number }
  | { ignored: false; pairs_removed: number; pairs_total: number };

export interface MergeAssetsInput {
  type: AssetMaintenanceType;
  keep_id: string;
  merge_ids: string[];
  keep_aliases?: string[] | null;
}

export interface MergedAssetReceipt {
  message: string;
  keep_id: string;
  merged_count: number;
  redirected_frames: number;
  fused_updated: number;
  aliases_added: number;
  dropped_images: Array<{ id: string; name: string }>;
  aliases: string[];
}

export interface AssetMaintenanceServicePort {
  checkOrphans(seriesId: string, input: AssetOrphanCheckInput, signal: AbortSignal): Promise<AssetOrphanCheckResponse>;
  listDuplicates(
    seriesId: string,
    type: AssetMaintenanceType,
    signal: AbortSignal,
  ): Promise<AssetDuplicateList>;
  setDuplicateIgnored(
    seriesId: string,
    input: SetAssetDuplicateIgnoredInput,
    signal: AbortSignal,
  ): Promise<SetAssetDuplicateIgnoredReceipt>;
  listIgnoredDuplicates(
    seriesId: string,
    type: AssetMaintenanceType,
    signal: AbortSignal,
  ): Promise<AssetIgnoredDuplicateList>;
  mergeAssets(seriesId: string, input: MergeAssetsInput, signal: AbortSignal): Promise<MergedAssetReceipt>;
}

export function toAssetMaintenanceType(type: AssetLibraryType): AssetMaintenanceType {
  switch (type) {
    case "characters": return "character";
    case "scenes": return "scene";
    case "props": return "prop";
  }
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new InvalidResponseError(`${label}格式无效。`);
  }
  return value as Record<string, unknown>;
}

function string(value: unknown, label: string): string {
  if (typeof value !== "string") throw new InvalidResponseError(`${label}格式无效。`);
  return value;
}

function requiredId(value: unknown, label: string): string {
  const id = string(value, label);
  if (id.trim() === "") throw new InvalidResponseError(`${label}不能为空。`);
  return id;
}

function nullableString(value: unknown, label: string): string | null {
  if (value === null) return null;
  return string(value, label);
}

function count(value: unknown, label: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    throw new InvalidResponseError(`${label}必须是非负整数。`);
  }
  return value as number;
}

function stringList(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) {
    throw new InvalidResponseError(`${label}格式无效。`);
  }
  return value as string[];
}

function maintenanceType(value: unknown, label: string): AssetMaintenanceType {
  if (value === "character" || value === "scene" || value === "prop") return value;
  throw new InvalidResponseError(`${label}类型无效。`);
}

export function parseAssetOrphanCheckResponse(value: unknown): AssetOrphanCheckResponse {
  const payload = record(value, "孤立项检查结果");
  if (!Array.isArray(payload.orphans)) throw new InvalidResponseError("孤立项列表格式无效。");
  return {
    orphans: payload.orphans.map((candidate) => {
      const item = record(candidate, "孤立项");
      return {
        type: maintenanceType(item.type, "孤立项"),
        id: requiredId(item.id, "孤立项编号"),
      };
    }),
  };
}

function parseDuplicateItem(value: unknown): AssetDuplicateItem {
  const item = record(value, "疑似重复素材");
  return {
    id: requiredId(item.id, "素材编号"),
    name: string(item.name, "素材名称"),
    description: string(item.description, "素材描述"),
    image_url: nullableString(item.image_url, "素材图片"),
    ref_count: count(item.ref_count, "引用次数"),
    aliases: stringList(item.aliases, "素材别名"),
    canonical_key: nullableString(item.canonical_key, "规范名称"),
  };
}

export function parseAssetDuplicateList(value: unknown, expectedType?: AssetMaintenanceType): AssetDuplicateList {
  const payload = record(value, "疑似重复结果");
  const type = maintenanceType(payload.type, "素材");
  if (expectedType !== undefined && type !== expectedType) {
    throw new InvalidResponseError("疑似重复结果的素材类型与请求不一致。");
  }
  if (!Array.isArray(payload.groups)) throw new InvalidResponseError("疑似重复组格式无效。");
  return {
    type,
    total: count(payload.total, "素材总数"),
    ignored_total: count(payload.ignored_total, "已忽略组合数"),
    groups: payload.groups.map((value) => {
      const group = record(value, "疑似重复组");
      if (!Number.isSafeInteger(group.index) || (group.index as number) < 0) {
        throw new InvalidResponseError("疑似重复组编号无效。");
      }
      if (!Array.isArray(group.items)) throw new InvalidResponseError("疑似重复组成员格式无效。");
      return { index: group.index as number, items: group.items.map(parseDuplicateItem) };
    }),
  };
}

export function parseAssetIgnoredDuplicateList(
  value: unknown,
  expectedType?: AssetMaintenanceType,
): AssetIgnoredDuplicateList {
  const payload = record(value, "已忽略重复结果");
  const type = maintenanceType(payload.type, "素材");
  if (expectedType !== undefined && type !== expectedType) {
    throw new InvalidResponseError("已忽略结果的素材类型与请求不一致。");
  }
  if (!Array.isArray(payload.items)) throw new InvalidResponseError("已忽略组合列表格式无效。");
  return {
    type,
    total: count(payload.total, "已忽略组合数"),
    items: payload.items.map((value) => {
      const item = record(value, "已忽略组合");
      return {
        id: requiredId(item.id, "已忽略组合编号"),
        asset_id_a: requiredId(item.asset_id_a, "素材编号"),
        asset_id_b: requiredId(item.asset_id_b, "素材编号"),
        name_a: string(item.name_a, "素材名称"),
        name_b: string(item.name_b, "素材名称"),
        image_a: nullableString(item.image_a, "素材图片"),
        image_b: nullableString(item.image_b, "素材图片"),
        created_at: nullableString(item.created_at, "创建时间"),
      };
    }),
  };
}

export function parseSetAssetDuplicateIgnoredReceipt(
  value: unknown,
  expectedIgnored?: boolean,
): SetAssetDuplicateIgnoredReceipt {
  const payload = record(value, "重复标记回执");
  if (typeof payload.ignored !== "boolean") throw new InvalidResponseError("重复标记回执格式无效。");
  if (expectedIgnored !== undefined && payload.ignored !== expectedIgnored) {
    throw new InvalidResponseError("重复标记回执与本次操作不一致。");
  }
  if (payload.ignored) {
    return {
      ignored: true,
      pairs_added: count(payload.pairs_added, "新增组合数"),
      pairs_total: count(payload.pairs_total, "组合总数"),
    };
  }
  return {
    ignored: false,
    pairs_removed: count(payload.pairs_removed, "恢复组合数"),
    pairs_total: count(payload.pairs_total, "组合总数"),
  };
}

export function parseMergedAssetReceipt(value: unknown, expectedKeepId?: string): MergedAssetReceipt {
  const payload = record(value, "素材合并回执");
  if (!Array.isArray(payload.dropped_images)) throw new InvalidResponseError("丢弃图片列表格式无效。");
  const keepId = requiredId(payload.keep_id, "保留素材编号");
  if (expectedKeepId !== undefined && keepId !== expectedKeepId) {
    throw new InvalidResponseError("合并回执的保留素材与本次操作不一致。");
  }
  return {
    message: string(payload.message, "合并说明"),
    keep_id: keepId,
    merged_count: count(payload.merged_count, "合并素材数"),
    redirected_frames: count(payload.redirected_frames, "改写镜头数"),
    fused_updated: count(payload.fused_updated, "融合成员更新数"),
    aliases_added: count(payload.aliases_added, "新增别名数"),
    dropped_images: payload.dropped_images.map((value) => {
      const item = record(value, "未保留图片");
      return { id: requiredId(item.id, "素材编号"), name: string(item.name, "素材名称") };
    }),
    aliases: stringList(payload.aliases, "合并后别名"),
  };
}
