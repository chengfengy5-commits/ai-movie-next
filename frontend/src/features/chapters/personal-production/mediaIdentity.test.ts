// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  digestPersonalProductionMediaIdentity,
  normalizePersonalProductionMediaIdentity,
} from "./mediaIdentity";

describe("personal production media identity", () => {
  it("filters only the frozen temporary signature keys and preserves the remaining query verbatim", () => {
    expect(normalizePersonalProductionMediaIdentity(
      " HTTPS://EXAMPLE.INVALID/a%2Fb?x=1&X-Amz-Signature=secret&k=a+b&signature=old#preview",
    )).toBe("https://EXAMPLE.INVALID/a%2Fb?x=1&k=a+b");
  });

  it("keeps unknown query names, original order, duplicate parameters and raw encoding", () => {
    expect(normalizePersonalProductionMediaIdentity("https://img.invalid/p?x=%2f&x=2&%73ignature=temp&sig=z#frag"))
      .toBe("https://img.invalid/p?x=%2f&x=2&sig=z");
  });

  it("handles relative paths, fragments, empty signatures and malformed escapes like the legacy client", () => {
    expect(normalizePersonalProductionMediaIdentity("/media/a?expires=1&v=2#part")).toBe("/media/a?v=2");
    expect(normalizePersonalProductionMediaIdentity("//[broken/path?signature=x")).toBe("//[broken/path?signature=x");
    expect(normalizePersonalProductionMediaIdentity("https://a.invalid/?%ZZ=x&Signature=y")).toBe("https://a.invalid/?%ZZ=x");
    expect(normalizePersonalProductionMediaIdentity("https://a.invalid/p?signature=")).toBe("https://a.invalid/p");
  });

  it("preserves interior control characters, normalizes a scheme only, and rejects empty identities", () => {
    expect(normalizePersonalProductionMediaIdentity("mailto:person@example.invalid#fragment"))
      .toBe("mailto:person@example.invalid");
    expect(normalizePersonalProductionMediaIdentity("HtTp://x.invalid/Path")).toBe("http://x.invalid/Path");
    expect(normalizePersonalProductionMediaIdentity("/a\u0007/b")).toBe("/a\u0007/b");
    expect(normalizePersonalProductionMediaIdentity("  \t ")).toBeNull();
    expect(normalizePersonalProductionMediaIdentity(null)).toBeNull();
  });

  it("computes a known SHA-256 locally without fetching media and reports an unavailable digest provider", async () => {
    await expect(digestPersonalProductionMediaIdentity("abc"))
      .resolves.toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    await expect(digestPersonalProductionMediaIdentity("", null)).resolves.toBeNull();
    await expect(digestPersonalProductionMediaIdentity("abc", null)).rejects.toThrow(/不支持本地素材摘要校验/);
  });
});
