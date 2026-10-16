import { ApiError } from "../../shared/api/errors";
import { resolveApiBaseUrl } from "../../shared/api/config";
import type { AdminFetcher } from "../admin/adminApi";
import {
  parseAdminMessage,
  parseAdminPurchasePackage,
  parseAdminPurchasePackagePage,
  parseAdminRedemptionCode,
  parseAdminRedemptionCodePage,
  parseGenerateRedemptionCodesReceipt,
} from "./adminMembershipParsers";
import type {
  AdminMembershipApi,
  PackageQuery,
  RedemptionCodeQuery,
  GenerateRedemptionCodesInput,
  PurchasePackageInput,
  PurchasePackageUpdate,
} from "./adminMembershipTypes";

function requestFailure(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if ((error instanceof DOMException && error.name === "AbortError")
    || (error instanceof Error && error.name === "AbortError")) {
    return new ApiError("aborted", "请求已取消。");
  }
  if (error instanceof Error && error.name === "TimeoutError") {
    return new ApiError("timeout", "请求超时。");
  }
  return new ApiError("network", "无法连接管理后台服务。");
}

async function responseDetail(response: Response): Promise<string> {
  try {
    const payload: unknown = await response.json();
    if (typeof payload === "object" && payload !== null && "detail" in payload
      && typeof payload.detail === "string" && payload.detail.length > 0) {
      return payload.detail;
    }
  } catch {
    // The HTTP status remains useful when the server returns no readable detail.
  }
  return "请求失败：HTTP " + response.status;
}

function makeQuery(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  return search.toString();
}

export function createAdminMembershipApi(
  fetcher: AdminFetcher,
  getToken: () => string | null,
  apiBaseUrl: string,
  pageUrl = globalThis.window?.location.href ?? "http://localhost/",
): AdminMembershipApi {
  const baseUrl = resolveApiBaseUrl(apiBaseUrl, pageUrl);

  async function request<T>(
    path: string,
    method: "GET" | "POST" | "PUT" | "DELETE",
    body: unknown,
    signal: AbortSignal | undefined,
    parse: (value: unknown) => T,
  ): Promise<T> {
    const token = getToken();
    if (!token) throw new ApiError("http", "未登录", 401, "未登录");
    const headers = new Headers();
    headers.set("Authorization", "Bearer " + token);
    if (body !== undefined) headers.set("Content-Type", "application/json");

    let response: Response;
    try {
      response = await fetcher(baseUrl + path, {
        method,
        headers,
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
        ...(signal ? { signal } : {}),
      });
    } catch (error) {
      throw requestFailure(error);
    }

    if (getToken() !== token) throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
    if (response.status !== 200) {
      const detail = await responseDetail(response);
      if (getToken() !== token) throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
      const kind = response.ok ? "invalid-response" : "http";
      throw new ApiError(kind, detail, response.status, detail);
    }

    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new ApiError("invalid-response", "服务返回的数据无法识别。");
    }
    if (getToken() !== token) throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
    return parse(payload);
  }

  return {
    listRedemptionCodes(query: RedemptionCodeQuery, signal?: AbortSignal) {
      const search = makeQuery({
        skip: query.skip,
        limit: query.limit,
        sort_field: query.sort_field,
        sort_order: query.sort_order,
        code: query.code === "" ? undefined : query.code,
        type: query.type === "" ? undefined : query.type,
        is_used: query.is_used,
        value: query.value,
      });
      return request("/admin/redemption-codes?" + search, "GET", undefined, signal, parseAdminRedemptionCodePage);
    },
    getRedemptionCode(id, signal) {
      return request("/admin/redemption-codes/" + encodeURIComponent(id), "GET", undefined, signal, (value) => {
        const record = parseAdminRedemptionCode(value);
        if (record.id !== id) throw new ApiError("invalid-response", "服务返回的兑换码 ID 与查询 ID 不一致。");
        return record;
      });
    },
    generateRedemptionCodes(input: GenerateRedemptionCodesInput, administratorId, signal) {
      return request(
        "/admin/redemption-codes",
        "POST",
        input,
        signal,
        (value) => parseGenerateRedemptionCodesReceipt(value, {
          type: input.type,
          value: input.value,
          count: input.count,
          administratorId,
        }),
      );
    },
    deleteRedemptionCode(id, signal) {
      return request("/admin/redemption-codes/" + encodeURIComponent(id), "DELETE", undefined, signal, parseAdminMessage);
    },
    listPurchasePackages(query: PackageQuery, signal) {
      const search = makeQuery({ skip: query.skip, limit: query.limit, sort_field: query.sort_field, sort_order: query.sort_order });
      return request("/admin/purchase-packages?" + search, "GET", undefined, signal, parseAdminPurchasePackagePage);
    },
    getPurchasePackage(id, signal) {
      return request("/admin/purchase-packages/" + encodeURIComponent(id), "GET", undefined, signal, (value) => {
        const record = parseAdminPurchasePackage(value);
        if (record.id !== id) throw new ApiError("invalid-response", "服务返回的套餐 ID 与查询 ID 不一致。");
        return record;
      });
    },
    createPurchasePackage(input: PurchasePackageInput, signal) {
      return request("/admin/purchase-packages", "POST", input, signal, (value) => {
        const record = parseAdminPurchasePackage(value);
        if (record.category !== input.category || record.name !== input.name || record.price !== input.price
          || record.credits !== input.credits || record.days !== input.days || record.sort_order !== input.sort_order
          || record.is_active !== input.is_active) {
          throw new ApiError("invalid-response", "服务返回的套餐与创建提交内容不一致。");
        }
        return record;
      });
    },
    updatePurchasePackage(id, input: PurchasePackageUpdate, signal) {
      return request("/admin/purchase-packages/" + encodeURIComponent(id), "PUT", input, signal, (value) => {
        const record = parseAdminPurchasePackage(value);
        if (record.id !== id || Object.entries(input).some(([field, expected]) =>
          record[field as keyof typeof record] !== expected)) {
          throw new ApiError("invalid-response", "服务返回的套餐与更新提交内容不一致。");
        }
        return record;
      });
    },
    deletePurchasePackage(id, signal) {
      return request("/admin/purchase-packages/" + encodeURIComponent(id), "DELETE", undefined, signal, parseAdminMessage);
    },
  };
}
