import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter } from "../../../shared/api/contracts";
import { buildChapterMediaExportQueue } from "./queue";
import {
  fingerprintMediaExportCandidate,
  mediaExportPreferencesStorageKey,
  mediaExportRecordIdentity,
  mediaExportRecordsStorageKey,
  readSavedMediaExportRecords,
  toSavedMediaExportRecord,
  writeSkipSavedPreference,
  type MediaExportRecordCandidate,
} from "./exportRecords";

const mocks = vi.hoisted(() => ({
  state: { status: "authenticated", user: { id: "actor-a" } },
  sessionGeneration: 1,
  services: {
    apiBaseUrl: "https://api.example.invalid/api",
    createMediaExportSession: vi.fn(),
  },
  dialogRef: { current: null as HTMLElement | null },
  dismiss: vi.fn(),
}));

vi.mock("../../auth/AuthProvider", () => ({
  useAuth: () => ({
    services: mocks.services,
    state: mocks.state,
    sessionGeneration: mocks.sessionGeneration,
  }),
}));

vi.mock("../../series/useSeriesDialogFocus", () => ({
  useSeriesDialogFocus: () => ({ dialogRef: mocks.dialogRef, dismiss: mocks.dismiss }),
}));

import { MediaExportDialog } from "./MediaExportDialog";

const realLocalStorage = window.localStorage;

const chapter: Chapter = {
  id: "chapter-a",
  series_id: "series-a",
  title: "第 1 集",
  content: [{
    text: "夜色笼罩街道",
    storyboard: ["asset-a"],
    preview: "https://cdn.example.invalid/clip.mp4?signature=do-not-store",
  }],
  order: 1,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "chapter-revision-4",
  lock: null,
};

const recordScope = {
  actorId: "actor-a",
  apiBaseUrl: "https://api.example.invalid/api",
  seriesId: chapter.series_id,
  chapterId: chapter.id,
} as const;

const preferenceScope = {
  actorId: recordScope.actorId,
  apiBaseUrl: recordScope.apiBaseUrl,
} as const;

function setMediaExportTransport(): void {
  mocks.services.createMediaExportSession.mockImplementation((isCurrent: () => boolean) => ({
    apiBaseUrl: recordScope.apiBaseUrl,
    isCurrent,
    classifyUrl: (value: string) => ({ kind: "anonymous-public", url: value }),
    resolveDownloadLinks: async (items: readonly { url: string; filename: string }[]) => (
      items.map((item) => ({ ...item, signed: false }))
    ),
    readMedia: async (url: string) => ({
      blob: new Blob(["clip"]),
      mimeType: "video/mp4",
      extension: "mp4",
      finalUrl: url,
    }),
  }));
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function makeDirectory(close: () => Promise<void> = async () => undefined) {
  const writer = {
    write: vi.fn(async () => undefined),
    close: vi.fn(close),
  };
  const file = { createWritable: async () => writer };
  const runDirectory = {
    async getDirectoryHandle() {
      throw new DOMException("missing", "NotFoundError");
    },
    async getFileHandle(_name: string, options?: { create?: boolean }) {
      if (options?.create === true) return file;
      throw new DOMException("missing", "NotFoundError");
    },
  };
  const parent = {
    async getDirectoryHandle(_name: string, options?: { create?: boolean }) {
      if (options?.create === true) return runDirectory;
      throw new DOMException("missing", "NotFoundError");
    },
    async getFileHandle() {
      throw new DOMException("missing", "NotFoundError");
    },
  };
  return { parent, writer };
}

function dialogElement() {
  return (
    <MediaExportDialog
      chapter={chapter}
      isSourceCurrent={() => true}
      onClose={vi.fn()}
      seriesName="雨夜"
      sourceMode="chapter"
      sourceRevision={chapter.updated_at}
    />
  );
}

function renderDialog() {
  return render(dialogElement());
}

function chapterFrameText(): string {
  const value = chapter.content?.[0]?.text;
  return typeof value === "string" ? value : "";
}

afterEach(() => {
  cleanup();
  Object.defineProperty(window, "localStorage", { configurable: true, value: realLocalStorage });
  realLocalStorage.clear();
  mocks.state.user.id = "actor-a";
  mocks.services.createMediaExportSession.mockReset();
  mocks.dialogRef.current = null;
  Object.defineProperty(window, "showDirectoryPicker", { configurable: true, value: undefined });
  vi.restoreAllMocks();
});

describe("MediaExportDialog local export records", () => {
  it("records a real directory save and skips it in the next queue", async () => {
    const user = userEvent.setup();
    setMediaExportTransport();
    const queue = buildChapterMediaExportQueue({
      runId: "media-export-record-preview",
      chapter,
      seriesName: "雨夜",
      selectedFrameIndexes: [0],
      sourceRevision: chapter.updated_at,
    });
    const item = queue.items[0]!;
    const candidate: MediaExportRecordCandidate = {
      sourceKind: item.sourceKind,
      assetId: item.assetId,
      sourceRevision: queue.sourceRevision,
      sourceUrl: item.sourceUrl,
      sourceText: chapterFrameText(),
      filename: item.filename,
    };
    const fingerprint = await fingerprintMediaExportCandidate(candidate);
    const record = toSavedMediaExportRecord(candidate, fingerprint);
    expect(record).not.toBeNull();
    if (record === null) throw new Error("The tested source should be eligible for a saved record.");
    expect(writeSkipSavedPreference(window.localStorage, preferenceScope, true)).toBe(true);

    const { parent, writer } = makeDirectory();
    Object.defineProperty(window, "showDirectoryPicker", {
      configurable: true,
      value: vi.fn(async () => parent),
    });

    const first = renderDialog();
    const dialog = within(await screen.findByRole("dialog", { name: "导出媒体素材" }));
    await waitFor(() => expect(dialog.getByRole("button", { name: "确认导出清单" })).toBeEnabled());
    await user.click(dialog.getByRole("button", { name: "确认导出清单" }));
    await user.click(dialog.getByRole("checkbox", { name: "我已核对本次选择的分镜与导出清单。" }));
    await user.click(dialog.getByRole("button", { name: "选择目录并开始" }));
    await screen.findByText(/本次队列已结束/u);

    expect(writer.write).toHaveBeenCalledOnce();
    expect(writer.close).toHaveBeenCalledOnce();
    const storedValue = window.localStorage.getItem(mediaExportRecordsStorageKey(recordScope));
    expect(storedValue).not.toBeNull();
    expect(storedValue).not.toContain(candidate.sourceUrl);
    expect(storedValue).not.toContain(candidate.sourceText);
    const storedRecords = readSavedMediaExportRecords(window.localStorage, recordScope);
    expect(storedRecords.status).toBe("ready");
    if (storedRecords.status !== "ready") return;
    expect(storedRecords.value.records).toEqual([record]);

    first.unmount();
    mocks.dialogRef.current = null;
    renderDialog();
    const nextDialog = within(await screen.findByRole("dialog", { name: "导出媒体素材" }));
    expect(await nextDialog.findByText("本机记录确认已保存，已跳过；文件仍需自行核对")).toBeInTheDocument();
    expect(nextDialog.getByRole("checkbox", { name: "选择分镜 1" })).toBeDisabled();
    expect(nextDialog.getByRole("button", { name: "确认导出清单" })).toBeDisabled();
    await user.click(nextDialog.getByRole("button", { name: /清除本章本机导出记录/u }));
    expect(await nextDialog.findByText("本章本机导出记录已清除；较早开始的写入不会恢复这些记录。"))
      .toBeInTheDocument();
    expect(nextDialog.getByRole("checkbox", { name: "选择分镜 1" })).toBeEnabled();
    expect(nextDialog.getByRole("button", { name: "确认导出清单" })).toBeEnabled();
    expect(mediaExportRecordIdentity(record.sourceKind, record.assetId)).toBe(
      mediaExportRecordIdentity(item.sourceKind, item.assetId!),
    );
    expect(mediaExportPreferencesStorageKey(preferenceScope)).toBeTruthy();
  });

  it("keeps a real export available when local storage reads fail", async () => {
    const user = userEvent.setup();
    setMediaExportTransport();
    const brokenStorage = {
      getItem() { throw new Error("storage denied"); },
      setItem() { throw new Error("storage denied"); },
      removeItem() { throw new Error("storage denied"); },
    } as unknown as Storage;
    Object.defineProperty(window, "localStorage", { configurable: true, value: brokenStorage });
    const { parent, writer } = makeDirectory();
    Object.defineProperty(window, "showDirectoryPicker", {
      configurable: true,
      value: vi.fn(async () => parent),
    });

    renderDialog();
    const dialog = within(await screen.findByRole("dialog", { name: "导出媒体素材" }));
    expect(await dialog.findByText(/本机导出记录或偏好不可用/u)).toBeInTheDocument();
    await waitFor(() => expect(dialog.getByRole("button", { name: "确认导出清单" })).toBeEnabled());
    await user.click(dialog.getByRole("button", { name: "确认导出清单" }));
    await user.click(dialog.getByRole("checkbox", { name: "我已核对本次选择的分镜与导出清单。" }));
    await user.click(dialog.getByRole("button", { name: "选择目录并开始" }));

    expect(await screen.findByText(/本次队列已结束/u)).toBeInTheDocument();
    expect(writer.write).toHaveBeenCalledOnce();
    expect(writer.close).toHaveBeenCalledOnce();
    expect(screen.getByText("已保存")).toBeInTheDocument();
    expect(screen.getByText("文件已确认保存到目标目录，但本机记录写入失败。"))
      .toBeInTheDocument();
  });

  it("shows a stale revision and leaves the changed source selectable", async () => {
    setMediaExportTransport();
    const queue = buildChapterMediaExportQueue({
      runId: "media-export-record-preview",
      chapter,
      seriesName: "雨夜",
      selectedFrameIndexes: [0],
      sourceRevision: "chapter-revision-3",
    });
    const item = queue.items[0]!;
    const candidate: MediaExportRecordCandidate = {
      sourceKind: item.sourceKind,
      assetId: item.assetId,
      sourceRevision: queue.sourceRevision,
      sourceUrl: item.sourceUrl,
      sourceText: chapterFrameText(),
      filename: item.filename,
    };
    const fingerprint = await fingerprintMediaExportCandidate(candidate);
    const record = toSavedMediaExportRecord(candidate, fingerprint);
    expect(record).not.toBeNull();
    if (record === null) throw new Error("The tested source should be eligible for a saved record.");
    window.localStorage.setItem(mediaExportRecordsStorageKey(recordScope), JSON.stringify({
      version: 1,
      generation: 0,
      records: [record],
    }));
    expect(writeSkipSavedPreference(window.localStorage, preferenceScope, true)).toBe(true);

    renderDialog();
    const dialog = within(await screen.findByRole("dialog", { name: "导出媒体素材" }));
    expect(await dialog.findByText("1 条本机记录与当前镜头或版本不符，已失效。"))
      .toBeInTheDocument();
    expect(dialog.getByRole("checkbox", { name: "选择分镜 1" })).toBeEnabled();
    expect(dialog.getByRole("button", { name: "确认导出清单" })).toBeEnabled();
    expect(dialog.getByText("已选，可导出")).toBeInTheDocument();
  });

  it("does not write a late saved receipt into a different actor's cache", async () => {
    const user = userEvent.setup();
    setMediaExportTransport();
    const close = deferred<void>();
    const { parent, writer } = makeDirectory(() => close.promise);
    Object.defineProperty(window, "showDirectoryPicker", {
      configurable: true,
      value: vi.fn(async () => parent),
    });

    const first = renderDialog();
    const dialog = within(await screen.findByRole("dialog", { name: "导出媒体素材" }));
    await waitFor(() => expect(dialog.getByRole("button", { name: "确认导出清单" })).toBeEnabled());
    await user.click(dialog.getByRole("button", { name: "确认导出清单" }));
    await user.click(dialog.getByRole("checkbox", { name: "我已核对本次选择的分镜与导出清单。" }));
    await user.click(dialog.getByRole("button", { name: "选择目录并开始" }));
    await waitFor(() => expect(writer.close).toHaveBeenCalledOnce());

    mocks.state.user.id = "actor-b";
    first.rerender(dialogElement());
    await act(async () => {
      close.resolve();
      await close.promise;
    });

    const nextActorScope = { ...recordScope, actorId: "actor-b" };
    expect(readSavedMediaExportRecords(realLocalStorage, recordScope)).toEqual({
      status: "ready",
      value: { generation: 0, records: [] },
    });
    expect(readSavedMediaExportRecords(realLocalStorage, nextActorScope)).toEqual({
      status: "ready",
      value: { generation: 0, records: [] },
    });
    expect(writer.write).toHaveBeenCalledOnce();
  });
});
