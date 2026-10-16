import { ApiError } from "../../shared/api/errors";
import { resolveApiBaseUrl } from "../../shared/api/config";
import type { AdminFetcher } from "../admin/adminApi";
import { parseAdminSmtpAccount, parseAdminSmtpAccountAck, parseAdminSmtpAccountCreateAck, parseAdminSmtpAccountPage } from "./adminSmtpAccountsParsers";
import type { AdminSmtpAccountCreateInput, AdminSmtpAccountUpdatePatch, AdminSmtpAccountsApi } from "./adminSmtpAccountsTypes";

function requestFailure(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if ((error instanceof DOMException && error.name === "AbortError")
    || (error instanceof Error && error.name === "AbortError")) return new ApiError("aborted", "请求已取消。");
  if (error instanceof Error && error.name === "TimeoutError") return new ApiError("timeout", "请求超时。");
  return new ApiError("network", "无法连接管理后台服务。");
}

export function createAdminSmtpAccountsApi(
  fetcher: AdminFetcher,
  getToken: () => string | null,
  getAdministratorId: () => string,
  apiBaseUrl: string,
  pageUrl = globalThis.window?.location.href ?? "http://localhost/",
): AdminSmtpAccountsApi {
  const baseUrl = resolveApiBaseUrl(apiBaseUrl, pageUrl);

  async function request<T>(
    path: string,
    method: "GET" | "POST" | "PUT" | "DELETE",
    body: unknown,
    signal: AbortSignal | undefined,
    parse: (payload: unknown) => T,
  ): Promise<T> {
    const token = getToken();
    const administratorId = getAdministratorId();
    if (!token) throw new ApiError("http", "未登录", 401, "未登录");
    const headers = new Headers({ Authorization: "Bearer " + token });
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
      if (getToken() !== token || getAdministratorId() !== administratorId) {
        throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
      }
      throw requestFailure(error);
    }
    if (getToken() !== token || getAdministratorId() !== administratorId) {
      throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
    }
    if (response.status !== 200) {
      const detail = "SMTP 操作失败（HTTP " + response.status + "）。";
      throw new ApiError(response.ok ? "invalid-response" : "http", detail, response.status, detail);
    }
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      if (getToken() !== token || getAdministratorId() !== administratorId) {
        throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
      }
      throw new ApiError("invalid-response", "服务返回的数据无法识别。", 200);
    }
    if (getToken() !== token || getAdministratorId() !== administratorId) {
      throw new ApiError("aborted", "登录会话已变化，旧请求结果已丢弃。");
    }
    return parse(payload);
  }

  return {
    listAccounts(signal) {
      return request("/admin/smtp-accounts", "GET", undefined, signal, parseAdminSmtpAccountPage);
    },
    getAccount(id, signal) {
      return request("/admin/smtp-accounts/" + encodeURIComponent(id), "GET", undefined, signal, (payload) => {
        const record = parseAdminSmtpAccount(payload);
        if (record.id !== id) {
          throw new ApiError("invalid-response", "服务返回的 SMTP 账号与请求不匹配。", 200);
        }
        return record;
      });
    },
    async createAccount(input: AdminSmtpAccountCreateInput, signal) {
      if (typeof input.user !== "string" || input.user === ""
        || typeof input.password !== "string" || input.password === ""
        || typeof input.host !== "string" || typeof input.port !== "number" || !Number.isSafeInteger(input.port)
        || typeof input.is_active !== "boolean") {
        throw new ApiError("invalid-response", "SMTP 创建内容无效，未发送请求。");
      }
      const body: AdminSmtpAccountCreateInput = {
        user: input.user,
        password: input.password,
        host: input.host,
        port: input.port,
        is_active: input.is_active,
      };
      return request("/admin/smtp-accounts", "POST", body, signal,
        (payload) => parseAdminSmtpAccountCreateAck(payload, body.user));
    },
    async updateAccount(id: string, patch: AdminSmtpAccountUpdatePatch, signal) {
      const allowedFields = new Set(["user", "host", "port", "is_active", "password"]);
      if (Object.keys(patch).some((field) => !allowedFields.has(field))) {
        throw new ApiError("invalid-response", "SMTP 更新包含不支持的字段，未发送请求。");
      }
      const body: AdminSmtpAccountUpdatePatch = {};
      if (patch.user !== undefined) {
        if (typeof patch.user !== "string") throw new ApiError("invalid-response", "SMTP 更新邮箱字段无效，未发送请求。");
        body.user = patch.user;
      }
      if (patch.host !== undefined) {
        if (typeof patch.host !== "string") throw new ApiError("invalid-response", "SMTP 更新主机字段无效，未发送请求。");
        body.host = patch.host;
      }
      if (patch.port !== undefined) {
        if (typeof patch.port !== "number" || !Number.isSafeInteger(patch.port)) throw new ApiError("invalid-response", "SMTP 更新端口字段无效，未发送请求。");
        body.port = patch.port;
      }
      if (patch.is_active !== undefined) {
        if (typeof patch.is_active !== "boolean") throw new ApiError("invalid-response", "SMTP 更新状态字段无效，未发送请求。");
        body.is_active = patch.is_active;
      }
      if (patch.password !== undefined) {
        if (typeof patch.password !== "string") throw new ApiError("invalid-response", "SMTP 授权码字段无效，未发送请求。");
        if (patch.password.includes("****")) throw new ApiError("invalid-response", "SMTP 授权码替换值不受支持，未发送请求。");
        if (patch.password !== "") body.password = patch.password;
      }
      if (Object.keys(body).length === 0) throw new ApiError("invalid-response", "SMTP 更新内容为空，未发送请求。");
      return request("/admin/smtp-accounts/" + encodeURIComponent(id), "PUT", body, signal,
        (payload) => parseAdminSmtpAccountAck(payload, "更新成功"));
    },
    deleteAccount(id, signal) {
      return request("/admin/smtp-accounts/" + encodeURIComponent(id), "DELETE", undefined, signal,
        (payload) => parseAdminSmtpAccountAck(payload, "删除成功"));
    },
  };
}
