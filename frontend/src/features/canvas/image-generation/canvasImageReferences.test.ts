import { afterEach, describe, expect, it, vi } from "vitest";
import {
  collectCanvasImageReferenceCandidates,
  readCanvasImageBlobAsDataUrl,
  projectCanvasImageReferenceConfiguration,
} from "./canvasImageReferences";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Canvas image references", () => {
  it("projects safe model limits and the provider cap", () => {
    expect(projectCanvasImageReferenceConfiguration(null)).toEqual({ enabled: true, maximum: 8, error: null });
    expect(projectCanvasImageReferenceConfiguration(JSON.stringify({
      max_reference_images: { options: [1, 4, 9], default: 9 },
    }))).toEqual({ enabled: true, maximum: 8, error: null });
    expect(projectCanvasImageReferenceConfiguration(JSON.stringify({
      max_reference_images: { options: [1, 3], default: 1 },
    }))).toEqual({ enabled: true, maximum: 1, error: null });
    expect(projectCanvasImageReferenceConfiguration(JSON.stringify({
      max_reference_images: { enabled: false },
    }))).toEqual({ enabled: false, maximum: 0, error: null });
    expect(projectCanvasImageReferenceConfiguration("{broken").error).not.toBeNull();
    expect(projectCanvasImageReferenceConfiguration(JSON.stringify({
      max_reference_images: { options: [2, 3], default: 1 },
    })).error).not.toBeNull();
  });

  it("settles safely when a FileReader load handler throws", async () => {
    class ThrowingResultFileReader extends EventTarget {
      get result(): string | ArrayBuffer | null {
        throw new Error("reader result is unavailable");
      }

      abort(): void {}

      readAsDataURL(): void {
        this.dispatchEvent(new Event("load"));
      }
    }
    vi.stubGlobal("FileReader", ThrowingResultFileReader as unknown as typeof FileReader);

    await expect(readCanvasImageBlobAsDataUrl(
      new Blob(["image"], { type: "image/png" }),
      new AbortController().signal,
    )).rejects.toMatchObject({ message: "参考图读取结果无法安全确认。" });
  });

  it("collects selected-node and either-direction edge images in stable order, without duplicates or video", () => {
    const candidates = collectCanvasImageReferenceCandidates([
      { ref: "frame:1", kind: "frame", title: "目标分镜", imageUrl: "https://image.example/frame.png" },
      { ref: "character:1", kind: "character", title: "角色", imageUrl: "https://image.example/character.png" },
      { ref: "scene:1", kind: "scene", title: "场景", imageUrl: "https://image.example/scene.png" },
      { ref: "prop:1", kind: "prop", title: "重复图片素材", imageUrl: "https://image.example/character.png" },
      { ref: "video:1", kind: "video", title: "视频", imageUrl: "https://image.example/video.png" },
      { ref: "pending:1", kind: "media", title: "待处理", imageUrl: null },
    ], [
      { sourceRef: "character:1", targetRef: "frame:1" },
      { sourceRef: "frame:1", targetRef: "scene:1" },
      { sourceRef: "prop:1", targetRef: "frame:1" },
      { sourceRef: "frame:1", targetRef: "video:1" },
      { sourceRef: "frame:1", targetRef: "pending:1" },
    ], "frame:1");

    expect(candidates.map(({ id, value }) => [id, value])).toEqual([
      ["frame:1", "https://image.example/frame.png"],
      ["character:1", "https://image.example/character.png"],
      ["scene:1", "https://image.example/scene.png"],
    ]);
    expect(candidates.map(({ name }) => name)).toEqual(["目标分镜", "角色", "场景"]);
    expect(candidates.every((candidate) => !candidate.value.includes("video"))).toBe(true);
  });
});
