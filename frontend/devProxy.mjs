const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);
const CONTROL_OR_WHITESPACE = /[\s\u0000-\u001f\u007f]/u;

function invalidTarget() {
  return new TypeError(
    "VITE_API_PROXY_TARGET must be an HTTP(S) loopback origin without credentials, path, query, or fragment.",
  );
}

function rawHostname(authority) {
  if (authority.startsWith("[")) {
    const closingBracket = authority.indexOf("]");
    if (closingBracket === -1) {
      throw invalidTarget();
    }

    const suffix = authority.slice(closingBracket + 1);
    if (suffix !== "" && !/^:\d+$/.test(suffix)) {
      throw invalidTarget();
    }
    return authority.slice(0, closingBracket + 1);
  }

  const firstColon = authority.indexOf(":");
  if (firstColon === -1) {
    return authority;
  }

  const suffix = authority.slice(firstColon);
  if (!/^:\d+$/.test(suffix)) {
    throw invalidTarget();
  }
  return authority.slice(0, firstColon);
}

export function resolveApiProxyTarget(value) {
  if (value === undefined) {
    return undefined;
  }
  if (
    typeof value !== "string"
    || value.length === 0
    || CONTROL_OR_WHITESPACE.test(value)
    || value.includes("\\")
    || value.includes("?")
    || value.includes("#")
  ) {
    throw invalidTarget();
  }

  const scheme = /^(https?):\/\//i.exec(value);
  if (!scheme) {
    throw invalidTarget();
  }

  const rest = value.slice(scheme[0].length);
  const pathStart = rest.indexOf("/");
  const authority = pathStart === -1 ? rest : rest.slice(0, pathStart);
  const path = pathStart === -1 ? "" : rest.slice(pathStart);
  if (
    authority === ""
    || authority.includes("@")
    || (path !== "" && path !== "/")
  ) {
    throw invalidTarget();
  }

  const configuredHostname = rawHostname(authority).toLowerCase();
  if (!LOOPBACK_HOSTS.has(configuredHostname)) {
    throw invalidTarget();
  }

  let target;
  try {
    target = new URL(value);
  } catch {
    throw invalidTarget();
  }

  if (
    (target.protocol !== "http:" && target.protocol !== "https:")
    || target.username !== ""
    || target.password !== ""
    || target.search !== ""
    || target.hash !== ""
    || target.pathname !== "/"
    || !LOOPBACK_HOSTS.has(target.hostname.toLowerCase())
  ) {
    throw invalidTarget();
  }

  return target.origin;
}

export function createApiProxyOptions(value) {
  const target = resolveApiProxyTarget(value);
  if (target === undefined) {
    return undefined;
  }

  return {
    target,
    changeOrigin: true,
  };
}

