import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter, Character } from "../../../shared/api/contracts";
import { ApiError } from "../../../shared/api/errors";
import { AssetReplacementError, type AssetReplacementPort } from "../../../shared/api/assetReplacement";
import type { WorkspaceServices } from "../../../shared/api/services";
import { demoSeries } from "../../series/demoSeries";
import { AssetReplacementDialog } from "./AssetReplacementDialog";

const series = demoSeries[0]!;

function character(id: string, name: string): Character {
  return {
    id,
    series_id: series.id,
    name,
    gender: null,
    age: null,
    role: null,
    appearance: null,
    description: null,
    image_url: null,
    audio_url: null,
    voice_ref: null,
    aliases: null,
    canonical_key: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
  };
}

function chapter(id: string, assetId: string): Chapter {
  return {
    id,
    series_id: series.id,
    title: `章节 ${id}`,
    content: [{ character: [assetId], text: `镜头 ${id}`, original_text: `原文 ${id}`, extra: { keep: true } }],
    order: 1,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    lock: null,
  };
}

function createServices(options: {
  port?: AssetReplacementPort;
  readChapters?: () => Chapter[] | Promise<Chapter[]>;
  listCharacters?: () => Character[];
} = {}): WorkspaceServices {
  return {
    mode: "api",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    restore: async () => null,
    login: async () => { throw new Error("Not used."); },
    listSeries: async () => [],
    listMyTeams: async () => [],
    listChapters: vi.fn(async () => options.readChapters?.() ?? [chapter("chapter-a", "old-id")]),
    listStoryboardAssets: async () => [],
    listCharacters: vi.fn(async () => options.listCharacters?.() ?? [character("old-id", "旧角色"), character("new-id", "新角色")]),
    listScenes: async () => [],
    listProps: async () => [],
    getPersonalProductionNotes: async () => { throw new Error("Unexpected notes request."); },
    getPersonalRoughCut: async () => { throw new Error("Unexpected rough-cut request."); },
    listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    logout: vi.fn(),
    ...(options.port === undefined ? {} : { assetReplacement: options.port }),
  };
}

function renderDialog(services: WorkspaceServices, open = true, onUnauthorized = vi.fn(), onBusyChange = vi.fn()) {
  return render(
    <AssetReplacementDialog
      asset={character("old-id", "旧角色")}
      canEnter
      category="characters"
      epoch={1}
      isCurrent={() => true}
      onBusyChange={onBusyChange}
      onClose={vi.fn()}
      onDirectoryUpdated={vi.fn()}
      onUnauthorized={onUnauthorized}
      open={open}
      seriesId={series.id}
      seriesName={series.name}
      services={services}
    />,
  );
}

async function chooseAndConfirmAll(): Promise<void> {
  const selector = await screen.findByRole("combobox", { name: "新角色" });
  await waitFor(() => expect(selector).toBeEnabled());
  fireEvent.change(selector, { target: { value: "new-id" } });
  await screen.findByRole("region", { name: "逐章替换预览" });
  const chapterChecks = screen.getAllByRole("checkbox", { name: /^选择章节 / });
  for (const checkbox of chapterChecks) fireEvent.click(checkbox);
  fireEvent.click(screen.getByRole("checkbox", { name: /我已核对所选章节/ }));
  fireEvent.click(screen.getByRole("button", { name: /确认并逐章替换/ }));
}

async function waitForChapterStatus(chapterId: string, status: string): Promise<void> {
  const row = await screen.findByRole("listitem", { name: new RegExp(`章节 ${chapterId}提交结果`) });
  await waitFor(() => expect(row.querySelector("strong")?.textContent).toContain(status));
}

describe("asset replacement dialog", () => {
  afterEach(() => cleanup());

  it("posts selected chapters serially and verifies each before advancing", async () => {
    const initial = [chapter("chapter-a", "old-id"), chapter("chapter-b", "old-id")];
    const current = new Map(initial.map((row) => [row.id, row.content]));
    const requests: string[] = [];
    let active = 0;
    let maxActive = 0;
    const port: AssetReplacementPort = {
      beginRun: () => ({
        isCurrent: () => true,
        async replaceChapter(chapterId, input) {
          requests.push(chapterId);
          expect(input).toEqual({ old_asset_id: "old-id", new_asset_id: "new-id", asset_type: "character" });
          active += 1;
          maxActive = Math.max(maxActive, active);
          await Promise.resolve();
          current.set(chapterId, [{ character: ["new-id"], text: `镜头 ${chapterId}`, original_text: `原文 ${chapterId}`, extra: { keep: true } }]);
          active -= 1;
          return { message: "已替换" };
        },
      }),
    };
    const services = createServices({
      port,
      readChapters: () => initial.map((row) => ({ ...row, content: current.get(row.id) ?? row.content })),
    });
    renderDialog(services);

    await chooseAndConfirmAll();

    await waitForChapterStatus("chapter-a", "引用已核对");
    await waitForChapterStatus("chapter-b", "引用已核对");
    expect(requests).toEqual(["chapter-a", "chapter-b"]);
    expect(maxActive).toBe(1);
    expect(services.listChapters).toHaveBeenCalledTimes(5);
  });

  it("keeps an unknown result after read-only recovery even when expected content is visible", async () => {
    let firstContent = chapter("chapter-a", "old-id").content;
    const secondContent = chapter("chapter-b", "old-id").content;
    const requests: string[] = [];
    const port: AssetReplacementPort = {
      beginRun: () => ({
        isCurrent: () => true,
        async replaceChapter(chapterId) {
          requests.push(chapterId);
          firstContent = chapter(chapterId, "new-id").content;
          throw new AssetReplacementError("unknown", "服务器响应中断，结果未知。", 500);
        },
      }),
    };
    const services = createServices({
      port,
      readChapters: () => [
        { ...chapter("chapter-a", "old-id"), content: firstContent },
        { ...chapter("chapter-b", "old-id"), content: secondContent },
      ],
    });
    renderDialog(services);

    await chooseAndConfirmAll();
    await waitForChapterStatus("chapter-a", "结果未知");
    fireEvent.click(screen.getByRole("button", { name: "重新核对（只读，不重发）" }));

    await screen.findByText(/当前引用符合预览预期；未知请求仍未获得服务器最终确认/);
    await waitForChapterStatus("chapter-a", "结果未知");
    await waitForChapterStatus("chapter-b", "未提交");
    expect(requests).toEqual(["chapter-a"]);
  });

  it("rechecks an acknowledged chapter after readback failure without posting again", async () => {
    let currentContent = chapter("chapter-a", "old-id").content;
    let chapterReads = 0;
    const replaceChapter = vi.fn(async (chapterId: string) => {
      currentContent = chapter(chapterId, "new-id").content;
      return { message: "已替换" };
    });
    const port: AssetReplacementPort = {
      beginRun: () => ({ isCurrent: () => true, replaceChapter }),
    };
    const services = createServices({
      port,
      readChapters: () => {
        chapterReads += 1;
        if (chapterReads === 3) throw new Error("readback unavailable");
        return [{ ...chapter("chapter-a", "old-id"), content: currentContent }];
      },
    });
    renderDialog(services);

    await chooseAndConfirmAll();
    await waitForChapterStatus("chapter-a", "服务已确认，待回读");
    fireEvent.click(screen.getByRole("button", { name: "重新核对（只读，不重发）" }));
    await waitForChapterStatus("chapter-a", "引用已核对");

    expect(replaceChapter).toHaveBeenCalledTimes(1);
    expect(chapterReads).toBe(4);
  });

  it("does not send when the chapter changes after preview", async () => {
    let reads = 0;
    const replaceChapter = vi.fn(async () => ({ message: "unexpected" }));
    const port: AssetReplacementPort = {
      beginRun: () => ({ isCurrent: () => true, replaceChapter }),
    };
    const services = createServices({
      port,
      readChapters: () => {
        reads += 1;
        const row = chapter("chapter-a", reads >= 2 ? "changed-id" : "old-id");
        return [row];
      },
    });
    renderDialog(services);

    await chooseAndConfirmAll();

    await screen.findByText("本章内容已变化，本章未发送；请重新生成预览并确认。");
    expect(replaceChapter).not.toHaveBeenCalled();
    expect(services.listChapters).toHaveBeenCalledTimes(2);
  });

  it("does not send when the selected asset changes after preview", async () => {
    let finishAttempt!: () => void;
    const attemptFinished = new Promise<void>((resolve) => { finishAttempt = resolve; });
    const onBusyChange = vi.fn((_epoch: number, busy: boolean) => {
      if (!busy) finishAttempt();
    });
    let directoryReads = 0;
    const replaceChapter = vi.fn(async () => ({ message: "unexpected" }));
    const port: AssetReplacementPort = {
      beginRun: () => ({ isCurrent: () => true, replaceChapter }),
    };
    const services = createServices({
      port,
      listCharacters: () => {
        directoryReads += 1;
        return directoryReads === 1
          ? [character("old-id", "旧角色"), character("new-id", "新角色")]
          : [
            character("old-id", "旧角色"),
            { ...character("new-id", "已改名的新角色"), description: "更新后的新素材内容" },
          ];
      },
    });
    renderDialog(services, true, vi.fn(), onBusyChange);

    await chooseAndConfirmAll();
    await attemptFinished;

    expect(replaceChapter).not.toHaveBeenCalled();
    expect(directoryReads).toBe(2);
  });

  it("does not dispatch with a new session token after the preview was created", async () => {
    let session = "preview-session";
    let finishAttempt!: () => void;
    const attemptFinished = new Promise<void>((resolve) => { finishAttempt = resolve; });
    const onBusyChange = vi.fn((_epoch: number, busy: boolean) => {
      if (!busy) finishAttempt();
    });
    const dispatched: string[] = [];
    const beginRun = vi.fn(() => {
      const boundSession = session;
      return {
        isCurrent: () => session === boundSession,
        async replaceChapter(chapterId: string) {
          dispatched.push(chapterId);
          return { message: "已替换" };
        },
      };
    });
    const services = createServices({ port: { beginRun } });
    renderDialog(services, true, vi.fn(), onBusyChange);

    const selector = await screen.findByRole("combobox", { name: "新角色" });
    await waitFor(() => expect(selector).toBeEnabled());
    fireEvent.change(selector, { target: { value: "new-id" } });
    await screen.findByRole("region", { name: "逐章替换预览" });
    fireEvent.click(await screen.findByRole("checkbox", { name: /^选择章节 / }));
    fireEvent.click(screen.getByRole("checkbox", { name: /我已核对所选章节/ }));

    session = "renewed-session";
    fireEvent.click(screen.getByRole("button", { name: /确认并逐章替换/ }));

    await attemptFinished;
    expect(dispatched).toEqual([]);
    expect(beginRun).toHaveBeenCalledTimes(1);
  });

  it("does not log out a renewed session when unknown-result readback returns the old session 401", async () => {
    let session = "original-session";
    let chapterReads = 0;
    let rejectLateRead!: (reason: unknown) => void;
    const lateRead = new Promise<Chapter[]>((_resolve, reject) => { rejectLateRead = reject; });
    const onUnauthorized = vi.fn();
    const port: AssetReplacementPort = {
      beginRun: () => {
        const boundSession = session;
        return {
          isCurrent: () => session === boundSession,
          async replaceChapter() {
            throw new AssetReplacementError("unknown", "原请求结果未知。", 500);
          },
        };
      },
    };
    const services = createServices({
      port,
      readChapters: () => {
        chapterReads += 1;
        if (chapterReads === 3) return lateRead;
        return [chapter("chapter-a", "old-id")];
      },
    });
    renderDialog(services, true, onUnauthorized);

    await chooseAndConfirmAll();
    await waitForChapterStatus("chapter-a", "结果未知");
    fireEvent.click(screen.getByRole("button", { name: "重新核对（只读，不重发）" }));
    await waitFor(() => expect(chapterReads).toBe(3));
    session = "renewed-session";
    rejectLateRead(new ApiError("http", "旧会话已失效。", 401));

    await screen.findByText(/只读核对失败|原预览会话已变化/);
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(chapterReads).toBe(3);
  });

  it("retains the result view across close and reopen without resending", async () => {
    let currentContent = chapter("chapter-a", "old-id").content;
    const requestIds: string[] = [];
    const port: AssetReplacementPort = {
      beginRun: () => ({
        isCurrent: () => true,
        async replaceChapter(chapterId) {
          requestIds.push(chapterId);
          currentContent = chapter(chapterId, "new-id").content;
          throw new AssetReplacementError("unknown", "连接中断，结果未知。", null);
        },
      }),
    };
    const services = createServices({ port, readChapters: () => [{ ...chapter("chapter-a", "old-id"), content: currentContent }] });
    const view = renderDialog(services);
    await chooseAndConfirmAll();
    await waitForChapterStatus("chapter-a", "结果未知");

    view.rerender(
      <AssetReplacementDialog
        asset={character("old-id", "旧角色")}
        canEnter
        category="characters"
        epoch={2}
        isCurrent={() => true}
        onBusyChange={vi.fn()}
        onClose={vi.fn()}
        onDirectoryUpdated={vi.fn()}
        onUnauthorized={vi.fn()}
        open={false}
        seriesId={series.id}
        seriesName={series.name}
        services={services}
      />,
    );
    view.rerender(
      <AssetReplacementDialog
        asset={character("old-id", "旧角色")}
        canEnter
        category="characters"
        epoch={3}
        isCurrent={() => true}
        onBusyChange={vi.fn()}
        onClose={vi.fn()}
        onDirectoryUpdated={vi.fn()}
        onUnauthorized={vi.fn()}
        open
        seriesId={series.id}
        seriesName={series.name}
        services={services}
      />,
    );

    await waitForChapterStatus("chapter-a", "结果未知");
    expect(requestIds).toEqual(["chapter-a"]);
  });
});
