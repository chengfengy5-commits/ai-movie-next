import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../shared/api/errors";
import type {
  PersonalRoughCutPlaybackReader,
  WorkspaceServices,
} from "../../shared/api/services";
import type {
  PersonalRoughCutFrame,
  PersonalRoughCutSnapshot,
} from "../../shared/api/personalRoughCut";
import {
  buildPersonalRoughCutPlaybackQueue,
  PersonalRoughCutPlayback,
} from "./PersonalRoughCutPlayback";

const apiBaseUrl = "http://127.0.0.1:4175/api";

function frame(
  assetId: string,
  frameIndex: number,
  overrides: Partial<PersonalRoughCutFrame> = {},
): PersonalRoughCutFrame {
  return {
    asset_id: assetId,
    frame_index: frameIndex,
    text: `镜头 ${assetId}`,
    preview_url: `/uploads/${assetId}.mp4`,
    missing_reason: null,
    included: true,
    pending: false,
    ...overrides,
  };
}

function snapshot(frames: PersonalRoughCutFrame[], saved = true): PersonalRoughCutSnapshot {
  return {
    chapter_id: "chapter-playback",
    revision: 4,
    saved,
    frames,
    removed_asset_ids: [],
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => {
    resolve = complete;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function makeReader(read = vi.fn(async (_url: string, _signal: AbortSignal) => new Blob(["clip"]))) {
  let current = true;
  const reader: PersonalRoughCutPlaybackReader = {
    isCurrent: () => current,
    read,
  };
  return {
    reader,
    read,
    setCurrent(value: boolean) { current = value; },
  };
}

function makeServices(): WorkspaceServices {
  return { mode: "api", apiBaseUrl } as WorkspaceServices;
}

function renderPlayback(options: {
  value?: PersonalRoughCutSnapshot;
  reader?: PersonalRoughCutPlaybackReader | null;
  services?: WorkspaceServices;
  onUnauthorized?: () => void;
} = {}) {
  const props = {
    chapterId: "chapter-playback",
    userId: "actor-playback",
    services: options.services ?? makeServices(),
    snapshot: options.value ?? snapshot([frame("first", 0), frame("second", 1)]),
    requestGeneration: 3,
    reader: options.reader === undefined ? makeReader().reader : options.reader,
    enabled: true,
    onUnauthorized: options.onUnauthorized ?? vi.fn(),
    registerStop: vi.fn(),
  };
  const view = render(<PersonalRoughCutPlayback {...props} />);
  return { ...view, props };
}

describe("personal rough-cut playback queue", () => {
  it("uses saved order and excludes rows with invalid, pending, missing, duplicate, or unsafe media", () => {
    const value = snapshot([
      frame("saved-second", 8),
      frame("excluded", 1, { included: false }),
      frame("pending", 2, { pending: true }),
      frame("missing", 3, { preview_url: null }),
      frame("remote", 4, { preview_url: "https://media.example.invalid/clip.mp4" }),
      frame("duplicate", 5),
      frame("duplicate", 6),
      frame("   ", 7),
      frame("saved-first", 0),
    ]);

    const queue = buildPersonalRoughCutPlaybackQueue(value, apiBaseUrl);

    expect(queue.frames.map((row) => row.asset_id)).toEqual(["saved-second", "saved-first"]);
    expect(queue).toMatchObject({
      excludedCount: 1,
      pendingCount: 1,
      missingCount: 1,
      unsupportedCount: 1,
      duplicateCount: 2,
      invalidIdentityCount: 1,
    });
    expect(buildPersonalRoughCutPlaybackQueue(snapshot([frame("unsaved", 0)], false), apiBaseUrl).frames)
      .toEqual([]);
  });
});

describe("PersonalRoughCutPlayback", () => {
  beforeEach(() => {
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    let nextObjectUrl = 0;
    class TestURL extends URL {
      static createObjectURL = vi.fn((_blob: Blob) => `blob:rough-cut-test/${++nextObjectUrl}`);
      static revokeObjectURL = vi.fn((_url: string) => undefined);
    }
    vi.stubGlobal("URL", TestURL);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("starts from a selected saved row and advances once when the current video ends", async () => {
    const read = vi.fn(async (_url: string, _signal: AbortSignal) => new Blob(["clip"]));
    const { reader } = makeReader(read);
    renderPlayback({ reader });

    fireEvent.click(screen.getAllByRole("button", { name: "从此镜试看" })[0]!);
    await waitFor(() => expect(read).toHaveBeenCalledWith("/uploads/first.mp4", expect.any(AbortSignal)));
    const video = await screen.findByLabelText("当前镜头视频预览") as HTMLVideoElement;
    await waitFor(() => expect(video.getAttribute("src")).toBe("blob:rough-cut-test/1"));

    fireEvent.ended(video);
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    expect(read.mock.calls[1]?.[0]).toBe("/uploads/second.mp4");
  });

  it.each(["ended", "error"] as const)(
    "does not let an old %s event consume the next attempt's loading media",
    async (oldEvent) => {
      const secondRead = deferred<Blob>();
      const read = vi.fn((url: string, _signal: AbortSignal) => {
        if (url === "/uploads/second.mp4") return secondRead.promise;
        return Promise.resolve(new Blob([url]));
      });
      const { reader } = makeReader(read);
      renderPlayback({
        value: snapshot([frame("first", 0), frame("second", 1), frame("third", 2)]),
        reader,
      });

      fireEvent.click(screen.getByRole("button", { name: "从头试看" }));
      await waitFor(() => expect(screen.getByLabelText("当前镜头视频预览").getAttribute("src"))
        .toBe("blob:rough-cut-test/1"));
      const firstVideo = screen.getByLabelText("当前镜头视频预览") as HTMLVideoElement;
      fireEvent.ended(firstVideo);
      await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
      const secondSignal = read.mock.calls[1]?.[1];

      if (oldEvent === "ended") {
        fireEvent.ended(firstVideo);
      } else {
        fireEvent.error(firstVideo);
      }

      expect(read).toHaveBeenCalledTimes(2);
      expect(secondSignal?.aborted).toBe(false);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();

      await act(async () => {
        secondRead.resolve(new Blob(["second clip"]));
        await secondRead.promise;
      });
      const secondVideo = await screen.findByLabelText("当前镜头视频预览") as HTMLVideoElement;
      await waitFor(() => expect(secondVideo.getAttribute("src")).toBe("blob:rough-cut-test/2"));
      expect(secondVideo).not.toBe(firstVideo);

      fireEvent.ended(secondVideo);
      await waitFor(() => expect(read).toHaveBeenCalledTimes(3));
      expect(read.mock.calls[2]?.[0]).toBe("/uploads/third.mp4");
    },
  );

  it("offers a user action after automatic play is rejected without re-reading the clip", async () => {
    const read = vi.fn(async (_url: string, _signal: AbortSignal) => new Blob(["clip"]));
    const { reader } = makeReader(read);
    vi.spyOn(HTMLMediaElement.prototype, "play")
      .mockRejectedValueOnce(new DOMException("Blocked", "NotAllowedError"))
      .mockResolvedValue(undefined);
    renderPlayback({ reader });

    fireEvent.click(screen.getByRole("button", { name: "从头试看" }));
    expect(await screen.findByRole("button", { name: "继续播放" })).toBeInTheDocument();
    expect(screen.getByText("浏览器尚未允许自动播放，请点击“继续播放”。")).toBeInTheDocument();
    expect(read).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByRole("button", { name: "继续播放" }));
    await waitFor(() => expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2));
    expect(read).toHaveBeenCalledOnce();
  });

  it("refuses a stale session before making a media request", () => {
    const read = vi.fn(async (_url: string, _signal: AbortSignal) => new Blob(["clip"]));
    const mutable = makeReader(read);
    mutable.setCurrent(false);
    const onUnauthorized = vi.fn();
    renderPlayback({ reader: mutable.reader, onUnauthorized });

    fireEvent.click(screen.getByRole("button", { name: "从头试看" }));

    expect(read).not.toHaveBeenCalled();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("登录状态已变化");
  });

  it("ignores an old 401 after the bound session becomes stale", async () => {
    let rejectRead!: (reason: unknown) => void;
    const read = vi.fn((_url: string, _signal: AbortSignal) => new Promise<Blob>((_resolve, reject) => {
      rejectRead = reject;
    }));
    const mutable = makeReader(read);
    const onUnauthorized = vi.fn();
    renderPlayback({ reader: mutable.reader, onUnauthorized });

    fireEvent.click(screen.getByRole("button", { name: "从头试看" }));
    await waitFor(() => expect(read).toHaveBeenCalledOnce());
    mutable.setCurrent(false);
    await act(async () => {
      rejectRead(new ApiError("http", "expired", 401));
      await Promise.resolve();
    });

    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("登录状态已变化");
  });

  it("does not fetch the next clip or retry through a stale canonical reader", async () => {
    const read = vi.fn(async (_url: string, _signal: AbortSignal) => new Blob(["clip"]));
    const mutable = makeReader(read);
    const onUnauthorized = vi.fn();
    renderPlayback({ reader: mutable.reader, onUnauthorized });

    fireEvent.click(screen.getByRole("button", { name: "从头试看" }));
    const video = await screen.findByLabelText("当前镜头视频预览") as HTMLVideoElement;
    await waitFor(() => expect(video.getAttribute("src")).toBe("blob:rough-cut-test/1"));
    mutable.setCurrent(false);
    fireEvent.ended(video);

    expect(screen.getByRole("alert")).toHaveTextContent("登录状态已变化");
    fireEvent.click(screen.getByRole("button", { name: "重试此镜" }));

    expect(read).toHaveBeenCalledOnce();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("aborts the active read and releases its own object URL when stopped", async () => {
    const read = vi.fn(async (_url: string, _signal: AbortSignal) => new Blob(["clip"]));
    const { reader } = makeReader(read);
    renderPlayback({ reader });
    fireEvent.click(screen.getByRole("button", { name: "从头试看" }));

    const video = await screen.findByLabelText("当前镜头视频预览") as HTMLVideoElement;
    await waitFor(() => expect(video.getAttribute("src")).toBe("blob:rough-cut-test/1"));
    const signal = read.mock.calls[0]?.[1];
    fireEvent.click(screen.getByRole("button", { name: "停止试看" }));

    expect(signal?.aborted).toBe(true);
    expect(video.getAttribute("src")).toBeNull();
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
    expect(HTMLMediaElement.prototype.load).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:rough-cut-test/1");
  });

  it("pauses and clears the attempt-owned media node when the component unmounts", async () => {
    const read = vi.fn(async (_url: string, _signal: AbortSignal) => new Blob(["clip"]));
    const { reader } = makeReader(read);
    const view = renderPlayback({ reader });
    fireEvent.click(screen.getByRole("button", { name: "从头试看" }));
    await waitFor(() => expect(screen.getByLabelText("当前镜头视频预览").getAttribute("src"))
      .toBe("blob:rough-cut-test/1"));
    const activeVideo = screen.getByLabelText("当前镜头视频预览") as HTMLVideoElement;
    const signal = read.mock.calls[0]?.[1];
    const pauseSpy = vi.mocked(HTMLMediaElement.prototype.pause);
    const loadSpy = vi.mocked(HTMLMediaElement.prototype.load);
    pauseSpy.mockClear();
    loadSpy.mockClear();

    view.unmount();

    expect(signal?.aborted).toBe(true);
    expect(pauseSpy).toHaveBeenCalledOnce();
    expect(loadSpy).toHaveBeenCalledOnce();
    expect(activeVideo.getAttribute("src")).toBeNull();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:rough-cut-test/1");
  });
});
