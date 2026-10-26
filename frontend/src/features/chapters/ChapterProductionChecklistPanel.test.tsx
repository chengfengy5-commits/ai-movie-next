import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Character, Chapter, FusedAsset, StoryboardAsset } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { WorkspaceServices } from "../../shared/api/services";
import { ChapterProductionChecklistPanel, type ChapterProductionChecklistOwner } from "./ChapterProductionChecklistPanel";

const seriesId = "checklist-series";
const originalCreateObjectUrl = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const originalRevokeObjectUrl = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
const originalClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");

function chapter(title = "清单章节"): Chapter {
  return {
    id: "checklist-chapter",
    series_id: seriesId,
    title,
    content: [{ storyboard: ["frame-1"], character: ["character-1"], preview: "https://media.example.invalid/preview.mp4" }],
    order: 1,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    lock: null,
  };
}

function character(overrides: Partial<Character> = {}): Character {
  return {
    id: "character-1",
    series_id: seriesId,
    name: "沈照",
    gender: null,
    age: null,
    role: "守门人",
    appearance: null,
    description: "不应复制到清单的描述",
    image_url: null,
    audio_url: null,
    voice_ref: "private-voice-reference",
    aliases: null,
    canonical_key: "private-canonical-key",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    ...overrides,
  };
}

function storyboardAsset(): StoryboardAsset {
  return {
    id: "frame-1",
    series_id: seriesId,
    chapter_id: "checklist-chapter",
    frame_index: 0,
    name: "分镜一",
    description: "不应出现在复制文本中",
    image_url: "https://media.example.invalid/frame.png",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
  };
}

function services(overrides: Partial<WorkspaceServices> = {}): WorkspaceServices {
  return {
    mode: "api",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    restore: async () => null,
    login: async () => { throw new Error("Not used by this panel test."); },
    listSeries: async () => [],
    listMyTeams: async () => [],
    listChapters: async () => [],
    listStoryboardAssets: async () => [storyboardAsset()],
    listCharacters: async () => [character()],
    listScenes: async () => [],
    listProps: async () => [],
    listFusedAssets: async () => [] as FusedAsset[],
    getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
    getPersonalRoughCut: async () => { throw new Error("Unexpected rough-cut request."); },
    listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    logout: vi.fn(),
    ...overrides,
  } as WorkspaceServices;
}

function renderPanel(
  currentChapter = chapter(),
  currentServices = services(),
  isCurrent = () => true,
) {
  const owner: ChapterProductionChecklistOwner = {
    userId: "checklist-user",
    seriesId,
    services: currentServices,
    chapter: currentChapter,
  };
  return render(
    <ChapterProductionChecklistPanel
      isCurrent={isCurrent}
      onClose={vi.fn()}
      onUnauthorized={vi.fn()}
      owner={owner}
    />,
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  if (originalCreateObjectUrl === undefined) delete (URL as unknown as { createObjectURL?: unknown }).createObjectURL;
  else Object.defineProperty(URL, "createObjectURL", originalCreateObjectUrl);
  if (originalRevokeObjectUrl === undefined) delete (URL as unknown as { revokeObjectURL?: unknown }).revokeObjectURL;
  else Object.defineProperty(URL, "revokeObjectURL", originalRevokeObjectUrl);
  if (originalClipboard === undefined) delete (navigator as unknown as { clipboard?: unknown }).clipboard;
  else Object.defineProperty(navigator, "clipboard", originalClipboard);
});

describe("chapter production checklist panel", () => {
  it("copies the allowlisted text and cleans the explicit TXT download", async () => {
    const user = userEvent.setup();
    const clipboardWrite = vi.fn(async (_text: string) => undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: clipboardWrite },
    });
    const createObjectUrl = vi.fn((_blob: Blob) => "blob:chapter-checklist");
    const revokeObjectUrl = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectUrl });
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const clearTimeout = vi.spyOn(window, "clearTimeout");
    const view = renderPanel(chapter("../unsafe:章节"));

    await screen.findByText("角色「沈照」：缺少图片（关联：分镜 1）");
    await user.click(screen.getByRole("button", { name: "复制清单" }));
    await waitFor(() => expect(clipboardWrite).toHaveBeenCalledTimes(1));
    const copiedText = clipboardWrite.mock.calls[0]?.[0] ?? "";
    expect(copiedText).toContain("角色「沈照」：缺少图片");
    expect(copiedText).not.toContain("https://");
    expect(copiedText).not.toContain("private-voice-reference");
    expect(copiedText).not.toContain("private-canonical-key");
    expect(copiedText).not.toContain("不应复制到清单");

    await user.click(screen.getByRole("button", { name: "导出 TXT" }));
    expect(createObjectUrl).toHaveBeenCalledTimes(1);
    expect(anchorClick).toHaveBeenCalledTimes(1);
    const download = document.querySelector<HTMLAnchorElement>('a[download="本章制作缺项-unsafe_章节.txt"]');
    expect(download).not.toBeNull();
    const blob = createObjectUrl.mock.calls[0]?.[0];
    expect(blob).toBeInstanceOf(Blob);
    await expect(blob?.text()).resolves.toBe(copiedText);

    view.unmount();
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:chapter-checklist");
    expect(clearTimeout).toHaveBeenCalled();
    expect(document.querySelector('a[download="本章制作缺项-unsafe_章节.txt"]')).toBeNull();
  });

  it("keeps failed directories unconfirmed and ignores a late result after scope invalidation", async () => {
    const pendingCharacters = deferred<Character[]>();
    const listCharacters = vi.fn()
      .mockRejectedValueOnce(new ApiError("network", "offline"))
      .mockImplementationOnce(() => pendingCharacters.promise);
    const currentServices = services({ listCharacters });
    let current = true;
    const view = renderPanel(chapter(), currentServices, () => current);

    expect(await screen.findByRole("alert")).toHaveTextContent("资产目录读取未完成，缺项暂不可确认。");
    expect(screen.getByRole("button", { name: "复制清单" })).toBeDisabled();
    await userEvent.setup().click(screen.getByRole("button", { name: "重新读取" }));
    await screen.findByText("正在读取章节和资产目录…");

    current = false;
    await act(async () => {
      pendingCharacters.resolve([character({ name: "迟到的旧账号素材" })]);
      await pendingCharacters.promise;
    });

    expect(screen.queryByText(/迟到的旧账号素材/)).toBeNull();
    expect(screen.queryByText(/当前已读取数据中暂无上述缺项/)).toBeNull();
    expect(screen.getByText("正在读取章节和资产目录…")).toBeInTheDocument();
    view.unmount();
  });
});
