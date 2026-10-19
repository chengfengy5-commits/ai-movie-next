import { ApiConfigurationError, resolveApiBaseUrl } from "./config";
import { InvalidResponseError, parseErrorDetail } from "./contracts";
import { ApiError, isAbortError } from "./errors";
import {
  parseCreatedTeam,
  parseCreatedTeamInvites,
  parseTeamDetail,
  parseTeamInviteList,
  parseTeamJoinReceipt,
  parseTeamMessage,
  parseTeamPermissionsReceipt,
  parseTeamRoleReceipt,
  parseTeamDirectory,
  parseUpdatedTeam,
  type TeamManagementServicePort,
  type TeamPermission,
  type TeamRole,
} from "./teamManagement";
import { API_TOKEN_KEY, browserStorage, type StorageLike } from "./storage";

export interface TeamManagementHttpOptions {
  storage?: StorageLike;
  fetcher?: typeof fetch;
  timeoutMs?: number;
  pageUrl?: string;
}

function requiredId(value: string, label: string): string {
  if (value.length === 0) {
    throw new ApiError("invalid-response", `${label}编号无效。`);
  }
  try {
    return encodeURIComponent(value);
  } catch {
    throw new ApiError("invalid-response", `${label}编号无效。`);
  }
}

function statusMessage(status: number, detail: string | null): string {
  if (detail !== null) return detail;
  if (status === 401) return "当前登录状态已失效，请重新登录。";
  if (status === 403) return "当前账号没有执行此团队操作的权限。";
  if (status === 404) return "团队或成员已不存在，请刷新后重试。";
  if (status === 429) return "请求过于频繁，请稍后再试。";
  if (status >= 500) return "团队服务暂时不可用，操作结果需要核对。";
  return "团队操作未完成，请检查输入并重试。";
}

export function createTeamManagementHttpPort(
  apiBaseUrl: string,
  options: TeamManagementHttpOptions = {},
): TeamManagementServicePort {
  const storage = options.storage ?? browserStorage();
  const fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
  const timeoutMs = options.timeoutMs ?? 15_000;
  let resolvedBaseUrl: string | null = null;

  function baseUrl(): string {
    if (resolvedBaseUrl === null) {
      try {
        resolvedBaseUrl = resolveApiBaseUrl(apiBaseUrl, options.pageUrl ?? "http://127.0.0.1/");
      } catch (error) {
        if (error instanceof ApiConfigurationError) {
          throw new ApiError("invalid-response", "团队 API 地址无效，无法发送团队请求。");
        }
        throw error;
      }
    }
    return resolvedBaseUrl;
  }

  function ensureCurrentSession(token: string, signal: AbortSignal): void {
    if (signal.aborted || storage.getItem(API_TOKEN_KEY) !== token) {
      throw new ApiError("aborted", "Request was cancelled.");
    }
  }

  async function request<T>(
    method: "GET" | "POST" | "PUT" | "DELETE",
    path: string,
    expectedStatus: 200 | 201,
    signal: AbortSignal,
    body: object | undefined,
    parse: (payload: unknown) => T,
  ): Promise<T> {
    const token = storage.getItem(API_TOKEN_KEY);
    if (token === null || token === "") {
      throw new ApiError("http", "当前登录状态已失效，请重新登录。", 401);
    }
    ensureCurrentSession(token, signal);

    const controller = new AbortController();
    const onAbort = () => controller.abort();
    signal.addEventListener("abort", onAbort, { once: true });
    let didTimeout = false;
    const timer = setTimeout(() => {
      didTimeout = true;
      controller.abort();
    }, timeoutMs);
    const headers = new Headers({
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
    });
    if (body !== undefined) headers.set("Content-Type", "application/json");

    try {
      const response = await fetcher(new URL(`${baseUrl()}${path}`), {
        method,
        headers,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal: controller.signal,
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer",
      });
      ensureCurrentSession(token, signal);
      if (didTimeout) throw new ApiError("timeout", "团队请求超时，操作结果需要核对。");
      let payload: unknown;
      try {
        payload = await response.json();
      } catch (error) {
        ensureCurrentSession(token, signal);
        if (!response.ok) {
          throw new ApiError("http", statusMessage(response.status, null), response.status);
        }
        if (isAbortError(error) && signal.aborted) {
          throw new ApiError("aborted", "Request was cancelled.");
        }
        if (didTimeout) throw new ApiError("timeout", "团队请求超时，操作结果需要核对。");
        throw new ApiError("invalid-response", "团队服务返回了无法识别的数据。", response.status);
      }
      ensureCurrentSession(token, signal);
      if (didTimeout) throw new ApiError("timeout", "团队请求超时，操作结果需要核对。");

      if (response.status !== expectedStatus) {
        if (response.status >= 400) {
          const detail = parseErrorDetail(payload);
          throw new ApiError("http", statusMessage(response.status, detail), response.status, detail);
        }
        throw new ApiError("invalid-response", "团队服务返回了与本次操作不匹配的状态。", response.status);
      }

      try {
        return parse(payload);
      } catch (error) {
        if (error instanceof ApiError) throw error;
        if (error instanceof InvalidResponseError) {
          throw new ApiError("invalid-response", error.message, response.status);
        }
        throw new ApiError("invalid-response", "团队服务返回的数据与本次操作不匹配。", response.status);
      }
    } catch (error) {
      ensureCurrentSession(token, signal);
      if (error instanceof ApiError) throw error;
      if (didTimeout) throw new ApiError("timeout", "团队请求超时，操作结果需要核对。");
      if (isAbortError(error)) throw new ApiError("aborted", "Request was cancelled.");
      throw new ApiError("network", "无法连接团队服务，操作结果需要核对。");
    } finally {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
    }
  }

  const noBody = (message: string) => (payload: unknown) => parseTeamMessage(payload, message);

  return {
    listTeams: (signal) => request("GET", "/teams/my", 200, signal, undefined, parseTeamDirectory),
    createTeam: (name, actorId, signal) => {
      if ([...name].length < 1 || [...name].length > 255 || actorId.length === 0) {
        throw new ApiError("invalid-response", "团队名称或当前账号信息无效。");
      }
      return request("POST", "/teams", 201, signal, { name }, (payload) => parseCreatedTeam(payload, name, actorId));
    },
    getTeam: (teamId, signal) => request(
      "GET", `/teams/${requiredId(teamId, "团队")}`, 200, signal, undefined,
      (payload) => parseTeamDetail(payload, teamId),
    ),
    updateTeam: (teamId, update, expectedName, signal) => {
      const body = update.name === undefined ? {} : { name: update.name };
      const expected = update.name === undefined || update.name === null ? expectedName : update.name.trim();
      return request(
        "PUT", `/teams/${requiredId(teamId, "团队")}`, 200, signal, body,
        (payload) => parseUpdatedTeam(payload, teamId, expected),
      );
    },
    deleteTeam: (teamId, signal) => request(
      "DELETE", `/teams/${requiredId(teamId, "团队")}`, 200, signal, undefined,
      noBody("团队已解散"),
    ),
    leaveTeam: (teamId, signal) => request(
      "POST", `/teams/${requiredId(teamId, "团队")}/leave`, 200, signal, undefined,
      noBody("已退出团队"),
    ),
    removeMember: (teamId, userId, signal) => request(
      "DELETE", `/teams/${requiredId(teamId, "团队")}/members/${requiredId(userId, "成员")}`,
      200, signal, undefined, noBody("成员已移除"),
    ),
    setMemberRole: (teamId, userId, role: TeamRole, currentRole, currentPermissions, signal) => request(
      "PUT", `/teams/${requiredId(teamId, "团队")}/members/${requiredId(userId, "成员")}/role`,
      200, signal, { role: role.trim().toLowerCase() },
      (payload) => parseTeamRoleReceipt(payload, role, currentRole, currentPermissions),
    ),
    setMemberPermissions: (teamId, userId, permissions: TeamPermission[], signal) => request(
      "PUT", `/teams/${requiredId(teamId, "团队")}/members/${requiredId(userId, "成员")}/permissions`,
      200, signal, { permissions },
      (payload) => parseTeamPermissionsReceipt(payload, permissions),
    ),
    createInvites: (teamId, count, signal) => {
      if (!Number.isInteger(count) || count < 1 || count > 5) {
        throw new ApiError("invalid-response", "邀请码数量必须为 1 到 5 个。");
      }
      return request(
        "POST", `/teams/${requiredId(teamId, "团队")}/invites`, 201, signal, { count },
        (payload) => parseCreatedTeamInvites(payload, count),
      );
    },
    listInvites: (teamId, signal) => request(
      "GET", `/teams/${requiredId(teamId, "团队")}/invites`, 200, signal, undefined, parseTeamInviteList,
    ),
    revokeInvite: (teamId, inviteId, signal) => request(
      "DELETE", `/teams/${requiredId(teamId, "团队")}/invites/${requiredId(inviteId, "邀请码")}`,
      200, signal, undefined, noBody("邀请码已作废"),
    ),
    joinTeam: (code, signal) => {
      const codeLength = [...code].length;
      if (codeLength < 6 || codeLength > 20) {
        throw new ApiError("invalid-response", "邀请码长度应为 6 到 20 个字符。");
      }
      return request("POST", "/teams/join", 200, signal, { code }, parseTeamJoinReceipt);
    },
  };
}
