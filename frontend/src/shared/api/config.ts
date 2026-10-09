export type AppMode = "demo" | "api";

export class ApiConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiConfigurationError";
  }
}

function isLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase();
  return normalized === "localhost" || normalized === "127.0.0.1" || normalized === "[::1]" || normalized === "::1";
}

function isLoopbackPage(pageUrl: string): boolean {
  try {
    const page = new URL(pageUrl);
    return (page.protocol === "http:" || page.protocol === "https:")
      && isLoopbackHostname(page.hostname);
  } catch {
    return false;
  }
}

function trimApiPath(pathname: string): string {
  return pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
}

export function resolveApiBaseUrl(
  configuredBaseUrl: string,
  pageUrl: string,
): string {
  if (configuredBaseUrl.trim() === "") {
    throw new ApiConfigurationError("API 模式需要配置隔离本地服务地址。");
  }
  if (!isLoopbackPage(pageUrl)) {
    throw new ApiConfigurationError("API 模式只能在本机回环地址打开，避免把会话信息发送到远端网页。");
  }

  let base: URL;
  try {
    base = new URL(configuredBaseUrl, pageUrl);
  } catch {
    throw new ApiConfigurationError("API 地址格式无效。");
  }

  if (base.protocol !== "http:" && base.protocol !== "https:") {
    throw new ApiConfigurationError("API 地址仅支持 HTTP 或 HTTPS。");
  }
  if (!isLoopbackHostname(base.hostname)) {
    throw new ApiConfigurationError("API 地址必须指向 localhost、127.0.0.1 或 ::1。");
  }
  if (base.username !== "" || base.password !== "") {
    throw new ApiConfigurationError("API 地址不能包含用户名或密码。");
  }
  if (base.search !== "" || base.hash !== "") {
    throw new ApiConfigurationError("API 地址不能包含查询参数或片段。");
  }
  if (trimApiPath(base.pathname) !== "/api") {
    throw new ApiConfigurationError("API 地址路径必须为 /api。");
  }

  return `${base.origin}/api`;
}

export interface EnvironmentConfiguration {
  mode: AppMode;
  apiBaseUrl?: string;
}

export function readEnvironmentConfiguration(
  environment: Record<string, string | undefined>,
  pageUrl: string,
): EnvironmentConfiguration {
  const mode = environment.VITE_AUTH_MODE ?? "demo";
  if (mode === "demo") {
    return { mode };
  }
  if (mode !== "api") {
    throw new ApiConfigurationError("VITE_AUTH_MODE 仅支持 demo 或 api。");
  }

  const apiBaseUrl = resolveApiBaseUrl(environment.VITE_API_BASE_URL ?? "", pageUrl);
  return { mode, apiBaseUrl };
}

export function isSafeCoverUrl(imageUrl: string | null, apiBaseUrl: string): string | null {
  if (!imageUrl) {
    return null;
  }
  try {
    const cover = new URL(imageUrl, apiBaseUrl);
    const api = new URL(apiBaseUrl);
    if (
      (cover.protocol === "http:" || cover.protocol === "https:")
      && isLoopbackHostname(cover.hostname)
      && cover.origin === api.origin
      && cover.username === ""
      && cover.password === ""
    ) {
      return cover.href;
    }
  } catch {
    return null;
  }
  return null;
}
