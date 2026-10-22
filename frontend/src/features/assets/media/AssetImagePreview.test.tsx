// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AssetImageSource, AssetImageUploadRun } from "../../../shared/api/assetImageUpload";
import { AssetImagePreview } from "./AssetImagePreview";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => { resolve = complete; });
  return { promise, resolve };
}

function source(): AssetImageSource {
  return Object.freeze({ kind: "asset-image-source" as const });
}

const urls = ["blob:asset-preview-1", "blob:asset-preview-2"];
let createdUrlIndex = 0;
let originalCreateDescriptor: PropertyDescriptor | undefined;
let originalRevokeDescriptor: PropertyDescriptor | undefined;

afterEach(() => {
  cleanup();
  if (originalCreateDescriptor === undefined) delete (URL as unknown as { createObjectURL?: unknown }).createObjectURL;
  else Object.defineProperty(URL, "createObjectURL", originalCreateDescriptor);
  if (originalRevokeDescriptor === undefined) delete (URL as unknown as { revokeObjectURL?: unknown }).revokeObjectURL;
  else Object.defineProperty(URL, "revokeObjectURL", originalRevokeDescriptor);
  originalCreateDescriptor = undefined;
  originalRevokeDescriptor = undefined;
  createdUrlIndex = 0;
  vi.restoreAllMocks();
});

describe("asset image preview", () => {
  it("binds load events to the request and node that created the image URL", async () => {
    originalCreateDescriptor = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    originalRevokeDescriptor = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => urls[createdUrlIndex++]),
    });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });

    const firstRead = deferred<Blob>();
    const secondRead = deferred<Blob>();
    const imageOne = source();
    const imageTwo = source();
    const run = {
      isCurrent: () => true,
      canSaveImage: () => false,
      canReadImage: () => true,
      initialImageSource: null,
      uploadImage: vi.fn(),
      saveImage: vi.fn(),
      readDirectory: vi.fn(),
      readImage: vi.fn()
        .mockReturnValueOnce(firstRead.promise)
        .mockReturnValueOnce(secondRead.promise),
    } as unknown as AssetImageUploadRun;
    const view = render(<AssetImagePreview alt="当前图片" run={run} source={imageOne} />);

    firstRead.resolve(new Blob(["one"], { type: "image/png" }));
    const staleNode = await screen.findByAltText("当前图片");
    expect(staleNode.className).not.toContain("loaded");

    view.rerender(<AssetImagePreview alt="当前图片" run={run} source={imageTwo} />);
    secondRead.resolve(new Blob(["two"], { type: "image/png" }));
    const currentNode = await screen.findByAltText("当前图片");
    await waitFor(() => expect(currentNode.getAttribute("src")).toBe(urls[1]));

    Object.defineProperty(currentNode, "naturalWidth", { configurable: true, value: 40 });
    Object.defineProperty(currentNode, "naturalHeight", { configurable: true, value: 30 });
    fireEvent.load(staleNode);
    expect(currentNode.className).not.toContain("loaded");
    fireEvent.load(currentNode);
    expect(currentNode.className).toContain("loaded");

    view.unmount();
    expect(revokeObjectURL.mock.calls).toEqual([[urls[0]], [urls[1]]]);
  });

  it("releases its own object URL immediately on decode failure and zero-pixel load", async () => {
    originalCreateDescriptor = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    originalRevokeDescriptor = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => urls[createdUrlIndex++]),
    });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    const run = {
      isCurrent: () => true,
      canSaveImage: () => false,
      canReadImage: () => true,
      initialImageSource: null,
      uploadImage: vi.fn(),
      saveImage: vi.fn(),
      readDirectory: vi.fn(),
      readImage: vi.fn(),
    } as unknown as AssetImageUploadRun;
    const firstFile = new File(["bad"], "broken.png", { type: "image/png" });
    const view = render(<AssetImagePreview alt="待上传图片" run={run} file={firstFile} />);
    const firstImage = await screen.findByAltText("待上传图片");
    await waitFor(() => expect(firstImage.getAttribute("src")).toBe(urls[0]));

    fireEvent.error(firstImage);
    expect(await screen.findByText("图片无法解码或显示。" )).toBeInTheDocument();
    expect(revokeObjectURL.mock.calls).toEqual([[urls[0]]]);

    const secondFile = new File(["blank"], "blank.png", { type: "image/png" });
    view.rerender(<AssetImagePreview alt="待上传图片" run={run} file={secondFile} />);
    const secondImage = await screen.findByAltText("待上传图片");
    await waitFor(() => expect(secondImage.getAttribute("src")).toBe(urls[1]));
    Object.defineProperty(secondImage, "naturalWidth", { configurable: true, value: 0 });
    Object.defineProperty(secondImage, "naturalHeight", { configurable: true, value: 0 });
    fireEvent.load(secondImage);

    expect(await screen.findByText("图片没有可显示的像素。" )).toBeInTheDocument();
    expect(revokeObjectURL.mock.calls).toEqual([[urls[0]], [urls[1]]]);
    view.unmount();
    expect(revokeObjectURL.mock.calls).toEqual([[urls[0]], [urls[1]]]);
  });

  it("does not create or publish an image URL after the run becomes stale", async () => {
    originalCreateDescriptor = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    originalRevokeDescriptor = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    const createObjectURL = vi.fn(() => urls[0]);
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const pending = deferred<Blob>();
    let current = true;
    const run = {
      isCurrent: () => current,
      canSaveImage: () => false,
      canReadImage: () => true,
      initialImageSource: null,
      uploadImage: vi.fn(),
      saveImage: vi.fn(),
      readDirectory: vi.fn(),
      readImage: vi.fn(() => pending.promise),
    } as unknown as AssetImageUploadRun;
    render(<AssetImagePreview alt="当前图片" run={run} source={source()} />);

    current = false;
    pending.resolve(new Blob(["image"], { type: "image/png" }));
    await waitFor(() => expect(screen.queryByAltText("当前图片")).not.toBeInTheDocument());
    expect(createObjectURL).not.toHaveBeenCalled();
  });
});
