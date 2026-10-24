// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import {
  createDirectoryMediaExportSink,
  createBrowserMediaExportSink,
  type DirectoryHandleLike,
} from "./sinks";
import { MediaExportSinkError } from "../../../shared/api/mediaExport";
import type { MediaExportSinkSaveContext } from "../../../shared/api/mediaExport";

const item = {
  id: "run-a:item-1",
  sourceKind: "chapter" as const,
  chapterId: "chapter-a",
  sourceIndex: 0,
  frameIndex: 0,
  assetId: "asset-a",
  sourceUrl: "https://cdn.example.invalid/clip.mp4",
  filename: "001_章节_剧集.mp4",
  eligibility: "ready" as const,
  state: "writing" as const,
  message: null,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function parentFor(runDirectory: DirectoryHandleLike): DirectoryHandleLike {
  return {
    async getDirectoryHandle(_name, options) {
      if (options?.create === true) return runDirectory;
      throw new DOMException("missing", "NotFoundError");
    },
    async getFileHandle() {
      throw new DOMException("missing", "NotFoundError");
    },
  };
}

function media() {
  return {
    blob: new Blob(["clip"]),
    mimeType: "video/mp4",
    extension: "mp4",
    finalUrl: "https://cdn.example.invalid/clip.mp4",
  };
}

describe("media export sinks", () => {
  it("requires a new run directory and records saved only after write and close", async () => {
    const file = { createWritable: vi.fn(async () => ({
      write: vi.fn(async () => undefined),
      close: vi.fn(async () => undefined),
    })) };
    const runDirectory: DirectoryHandleLike = {
      getDirectoryHandle: async () => {
        throw new DOMException("missing", "NotFoundError");
      },
      getFileHandle: vi.fn(async (_name: string, options?: { create?: boolean }) => {
        if (options?.create === true) return file;
        throw new DOMException("missing", "NotFoundError");
      }),
    };
    const parent: DirectoryHandleLike = {
      getDirectoryHandle: vi.fn(async (_name: string, options?: { create?: boolean }) => {
        if (options?.create === true) return runDirectory;
        throw new DOMException("missing", "NotFoundError");
      }),
      getFileHandle: async () => {
        throw new DOMException("missing", "NotFoundError");
      },
    };
    const sink = await createDirectoryMediaExportSink({
      runId: "run-a",
      pickDirectory: async () => parent,
    });

    await expect(sink.save(item, {
      blob: new Blob(["clip"]),
      mimeType: "video/mp4",
      extension: "mp4",
      finalUrl: "https://cdn.example.invalid/clip.mp4",
    })).resolves.toEqual({ kind: "saved" });
    expect(parent.getDirectoryHandle).toHaveBeenCalledWith("media-export-run-a", { create: true });
  });

  it("does not overwrite an existing file and marks close failure as unknown", async () => {
    const existing = { createWritable: vi.fn() };
    const directory = {
      getDirectoryHandle: vi.fn(async (_name: string, options?: { create?: boolean }) => {
        if (options?.create === true) return runDirectory;
        throw new DOMException("missing", "NotFoundError");
      }),
      getFileHandle: vi.fn(async () => existing),
    };
    const runDirectory = directory;
    const sink = await createDirectoryMediaExportSink({
      runId: "run-b",
      pickDirectory: async () => directory,
    });
    const media = {
      blob: new Blob(["clip"]),
      mimeType: "video/mp4",
      extension: "mp4",
      finalUrl: "https://cdn.example.invalid/clip.mp4",
    };
    await expect(sink.save(item, media)).rejects.toBeInstanceOf(MediaExportSinkError);
    expect(existing.createWritable).not.toHaveBeenCalled();

    const writer = {
      write: vi.fn(async () => undefined),
      close: vi.fn(async () => { throw new Error("disk error"); }),
      abort: vi.fn(async () => undefined),
    };
    const newFileDirectory = {
      getDirectoryHandle: vi.fn(async (_name: string, options?: { create?: boolean }) => {
        if (options?.create === true) return newFileDirectory;
        throw new DOMException("missing", "NotFoundError");
      }),
      getFileHandle: vi.fn(async (_name: string, options?: { create?: boolean }) => {
        if (options?.create === true) return { createWritable: async () => writer };
        throw new DOMException("missing", "NotFoundError");
      }),
    };
    const closeFailureSink = await createDirectoryMediaExportSink({
      runId: "run-c",
      pickDirectory: async () => newFileDirectory,
    });
    await expect(closeFailureSink.save(item, media)).rejects.toMatchObject({
      kind: "unknown",
      stage: "close",
    });
    expect(writer.write).toHaveBeenCalledOnce();
    expect(writer.close).toHaveBeenCalledOnce();
    expect(writer.abort).toHaveBeenCalledOnce();
  });

  it("does not create a file after scope changes during the file lookup", async () => {
    let current = true;
    const lookup = deferred<never>();
    const file = { createWritable: vi.fn() };
    const runDirectory: DirectoryHandleLike = {
      async getDirectoryHandle() {
        throw new DOMException("missing", "NotFoundError");
      },
      getFileHandle: vi.fn((_name: string, options?: { create?: boolean }) => {
        if (options?.create === true) return Promise.resolve(file);
        return lookup.promise;
      }),
    };
    const sink = await createDirectoryMediaExportSink({
      runId: "run-lookup",
      pickDirectory: async () => parentFor(runDirectory),
    });
    const context: MediaExportSinkSaveContext = {
      signal: new AbortController().signal,
      isCurrent: () => current,
    };
    const saving = sink.save(item, media(), context);
    await vi.waitFor(() => expect(runDirectory.getFileHandle).toHaveBeenCalledOnce());
    current = false;
    lookup.reject(new DOMException("missing", "NotFoundError"));

    await expect(saving).rejects.toMatchObject({ kind: "failed", stage: "write" });
    expect(runDirectory.getFileHandle).toHaveBeenCalledOnce();
    expect(file.createWritable).not.toHaveBeenCalled();
  });

  it("aborts a late writer before its first write when createWritable resolves after scope change", async () => {
    const writableHandle = deferred<{
      write(data: Blob): Promise<void>;
      close(): Promise<void>;
      abort(): Promise<void>;
    }>();
    const writer = {
      write: vi.fn(async () => undefined),
      close: vi.fn(async () => undefined),
      abort: vi.fn(async () => undefined),
    };
    const file = { createWritable: vi.fn(() => writableHandle.promise) };
    const runDirectory: DirectoryHandleLike = {
      async getDirectoryHandle() {
        throw new DOMException("missing", "NotFoundError");
      },
      async getFileHandle(_name, options) {
        if (options?.create === true) return file;
        throw new DOMException("missing", "NotFoundError");
      },
    };
    const sink = await createDirectoryMediaExportSink({
      runId: "run-writer-scope",
      pickDirectory: async () => parentFor(runDirectory),
    });
    let current = true;
    const saving = sink.save(item, media(), {
      signal: new AbortController().signal,
      isCurrent: () => current,
    });
    await vi.waitFor(() => expect(file.createWritable).toHaveBeenCalledOnce());
    current = false;
    writableHandle.resolve(writer);

    await expect(saving).rejects.toMatchObject({ kind: "unknown", stage: "write" });
    expect(writer.abort).toHaveBeenCalledOnce();
    expect(writer.write).not.toHaveBeenCalled();
    expect(writer.close).not.toHaveBeenCalled();
  });

  it("aborts the writable after cancellation during write and preserves an unknown result", async () => {
    const write = deferred<void>();
    const writer = {
      write: vi.fn(() => write.promise),
      close: vi.fn(async () => undefined),
      abort: vi.fn(async () => { write.reject(new Error("aborted")); }),
    };
    const file = { createWritable: vi.fn(async () => writer) };
    const runDirectory: DirectoryHandleLike = {
      async getDirectoryHandle() {
        throw new DOMException("missing", "NotFoundError");
      },
      async getFileHandle(_name, options) {
        if (options?.create === true) return file;
        throw new DOMException("missing", "NotFoundError");
      },
    };
    const sink = await createDirectoryMediaExportSink({
      runId: "run-write-abort",
      pickDirectory: async () => parentFor(runDirectory),
    });
    const controller = new AbortController();
    const saving = sink.save(item, media(), {
      signal: controller.signal,
      isCurrent: () => true,
    });
    await vi.waitFor(() => expect(writer.write).toHaveBeenCalledOnce());
    controller.abort();

    await expect(saving).rejects.toMatchObject({ kind: "unknown", stage: "write" });
    expect(writer.abort).toHaveBeenCalledOnce();
    expect(writer.close).not.toHaveBeenCalled();
  });

  it("keeps a completed close result after the old scope changes", async () => {
    const close = deferred<void>();
    const writer = {
      write: vi.fn(async () => undefined),
      close: vi.fn(() => close.promise),
      abort: vi.fn(async () => undefined),
    };
    const file = { createWritable: vi.fn(async () => writer) };
    const runDirectory: DirectoryHandleLike = {
      async getDirectoryHandle() {
        throw new DOMException("missing", "NotFoundError");
      },
      async getFileHandle(_name, options) {
        if (options?.create === true) return file;
        throw new DOMException("missing", "NotFoundError");
      },
    };
    const sink = await createDirectoryMediaExportSink({
      runId: "run-close-scope",
      pickDirectory: async () => parentFor(runDirectory),
    });
    let current = true;
    const saving = sink.save(item, media(), {
      signal: new AbortController().signal,
      isCurrent: () => current,
    });
    await vi.waitFor(() => expect(writer.close).toHaveBeenCalledOnce());
    current = false;
    close.resolve();

    await expect(saving).resolves.toEqual({ kind: "saved" });
    expect(writer.abort).not.toHaveBeenCalled();
  });

  it("labels browser downloads as initiated and releases object URLs after the click", async () => {
    const anchor = { href: "", download: "", click: vi.fn(), remove: vi.fn() };
    const append = vi.fn();
    const createObjectURL = vi.fn(() => "blob:media-export");
    const revokeObjectURL = vi.fn();
    const schedule = vi.fn((callback: () => void) => { callback(); return 1; });
    const sink = createBrowserMediaExportSink({
      createAnchor: () => anchor,
      appendAnchor: append,
      createObjectURL,
      revokeObjectURL,
      scheduleRevoke: schedule,
    });

    await expect(sink.save(item, {
      blob: new Blob(["clip"]),
      mimeType: "video/mp4",
      extension: "mp4",
      finalUrl: "https://cdn.example.invalid/clip.mp4",
    })).resolves.toEqual({ kind: "download-started" });
    expect(anchor.download).toBe(item.filename);
    expect(anchor.click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:media-export");
  });
});
