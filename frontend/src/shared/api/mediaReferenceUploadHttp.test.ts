import { describe, expect, it, vi } from "vitest";
import { createApiServices } from "./services";
import { API_TOKEN_KEY, API_USER_KEY, type StorageLike } from "./storage";
import { createMediaReferenceUploadHttpPort } from "./mediaReferenceUploadHttp";

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

function authenticatedStorage(): MemoryStorage {
  const storage = new MemoryStorage();
  storage.setItem(API_TOKEN_KEY, "session-token-948");
  storage.setItem(API_USER_KEY, JSON.stringify({ id: "actor-948" }));
  return storage;
}

describe("media reference upload HTTP port", () => {
  it("is available from the authenticated API services factory", () => {
    const services = createApiServices("http://127.0.0.1:4175/api", {
      storage: authenticatedStorage(),
      fetcher: vi.fn<typeof fetch>(),
    });

    expect(services.mediaReferenceUpload).toBeDefined();
  });

  it("uploads one selected image as multipart file and returns only a safe URL", async () => {
    const file = new File(["image bytes"], "reference.png", { type: "image/png" });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(Response.json({ url: "https://cdn.example.test/reference.png" }));
    const port = createMediaReferenceUploadHttpPort("http://127.0.0.1:4175/api", {
      storage: authenticatedStorage(),
      fetcher,
    });
    const run = port.beginRun({ actorId: "actor-948", isContextCurrent: () => true });

    await expect(run.upload("image", file, new AbortController().signal)).resolves.toBe("https://cdn.example.test/reference.png");

    expect(fetcher).toHaveBeenCalledOnce();
    const [requestInput, requestInit] = fetcher.mock.calls[0]!;
    expect(new URL(String(requestInput)).pathname).toBe("/api/upload/image");
    expect(requestInit?.method).toBe("POST");
    expect(new Headers(requestInit?.headers).get("Authorization")).toBe("Bearer session-token-948");
    expect(new Headers(requestInit?.headers).has("Content-Type")).toBe(false);
    const body = requestInit?.body as FormData;
    expect([...body.keys()]).toEqual(["file"]);
    const uploaded = body.get("file") as File;
    expect(uploaded.name).toBe(file.name);
    expect(uploaded.type).toBe(file.type);
    expect(uploaded.size).toBe(file.size);
  });
});
