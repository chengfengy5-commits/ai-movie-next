const temporaryMediaParameters = new Set([
  "ossaccesskeyid",
  "signature",
  "expires",
  "security-token",
  "awsaccesskeyid",
  "x-oss-signature-version",
  "x-oss-credential",
  "x-oss-date",
  "x-oss-expires",
  "x-oss-security-token",
  "x-oss-signature",
  "x-amz-algorithm",
  "x-amz-credential",
  "x-amz-date",
  "x-amz-expires",
  "x-amz-signedheaders",
  "x-amz-signature",
  "x-amz-security-token",
  "x-amz-content-sha256",
  "x-amz-region-set",
]);

export function normalizePersonalProductionMediaIdentity(value: string | null): string | null {
  if (typeof value !== "string" || value.trim() === "") {
    return null;
  }

  const source = value.replace(/^[\u0000-\u0020]+/, "");
  if ([...source].some((character) => {
    const codePoint = character.codePointAt(0);
    return codePoint !== undefined && (codePoint < 32 || codePoint === 127);
  })) {
    return value;
  }

  const scheme = /^([A-Za-z][A-Za-z0-9+.-]*):/.exec(source);
  const authoritySource = scheme === null ? source : source.slice(scheme[0].length);
  if (authoritySource.startsWith("//")) {
    const authority = authoritySource.slice(2).split(/[/?#]/, 1)[0] ?? "";
    const hostPort = authority.slice(authority.lastIndexOf("@") + 1);
    if (hostPort.includes("[") || hostPort.includes("]")) {
      const closeIndex = hostPort.indexOf("]");
      if (
        !hostPort.startsWith("[")
        || closeIndex <= 1
        || hostPort.indexOf("[", 1) !== -1
        || hostPort.indexOf("]", closeIndex + 1) !== -1
        || (hostPort.length > closeIndex + 1 && hostPort[closeIndex + 1] !== ":")
      ) {
        return value;
      }
      try {
        new URL("http://" + hostPort.slice(0, closeIndex + 1) + "/");
      } catch {
        return value;
      }
    }
  }

  const fragmentIndex = source.indexOf("#");
  const withoutFragment = fragmentIndex < 0 ? source : source.slice(0, fragmentIndex);
  const queryIndex = withoutFragment.indexOf("?");
  const schemeName = scheme?.[1];
  const schemeAdjusted = schemeName === undefined
    ? withoutFragment
    : schemeName.toLowerCase() + ":" + withoutFragment.slice(scheme?.[0].length ?? 0);
  if (queryIndex < 0) {
    return schemeAdjusted;
  }

  const path = schemeAdjusted.slice(0, queryIndex);
  const query = withoutFragment.slice(queryIndex + 1);
  if (query === "") {
    return path;
  }
  const retained = query.split("&").filter((parameter) => {
    const rawName = parameter.split("=", 1)[0] ?? "";
    let name: string;
    try {
      name = decodeURIComponent(rawName.replace(/\+/g, " ")).toLowerCase();
    } catch {
      name = rawName.toLowerCase();
    }
    return !temporaryMediaParameters.has(name);
  });
  return retained.length === 0 ? path : path + "?" + retained.join("&");
}

export async function digestPersonalProductionMediaIdentity(
  value: string | null,
  subtle: SubtleCrypto | null = globalThis.crypto?.subtle ?? null,
): Promise<string | null> {
  const identity = normalizePersonalProductionMediaIdentity(value);
  if (identity === null) {
    return null;
  }
  if (subtle === null || typeof subtle.digest !== "function") {
    throw new Error("当前环境不支持本地素材摘要校验。");
  }
  const digest = await subtle.digest("SHA-256", new TextEncoder().encode(identity));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
