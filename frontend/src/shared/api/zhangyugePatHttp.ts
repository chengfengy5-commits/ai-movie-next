import { ApiError } from "./errors";
import type {
  ZhangyugePatJsonRequester,
  ZhangyugePatServicePort,
  ZhangyugePatStatus,
} from "./zhangyugePat";

function invalidResponse(): ApiError {
  return new ApiError("invalid-response", "章鱼哥 PAT 服务返回的数据无法识别。", 200);
}

function record(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw invalidResponse();
  return value as Record<string, unknown>;
}

export function parseZhangyugePatStatus(payload: unknown): ZhangyugePatStatus {
  const source = record(payload);
  if (typeof source.configured !== "boolean") throw invalidResponse();
  return { configured: source.configured };
}

export function parseZhangyugePatSaveReceipt(payload: unknown): void {
  if (record(payload).ok !== true) throw invalidResponse();
}

export function createZhangyugePatHttp(requestJson: ZhangyugePatJsonRequester): ZhangyugePatServicePort {
  return {
    async getStatus(signal) {
      return parseZhangyugePatStatus(await requestJson({ endpoint: "zhangyugePatStatus" }, signal));
    },
    async save(pat, signal) {
      parseZhangyugePatSaveReceipt(await requestJson({ endpoint: "zhangyugePatSave", pat }, signal));
    },
  };
}
