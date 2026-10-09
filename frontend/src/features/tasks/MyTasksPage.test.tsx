import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter, Character, Prop, Scene, Series, StoryboardAsset, User } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { WorkspaceServices } from "../../shared/api/services";
import { MY_TASK_PAGE_SIZE, type MyTaskPage, type MyTaskRecord } from "../../shared/api/myTasks";
import { MyTasksPage } from "./MyTasksPage";

const user: User = {
  id: "fixture-user",
  username: "演示创作者",
  email: "fixture@example.invalid",
  is_superuser: false,
  membership_type: "demo",
  membership_expires_at: null,
  avatar_url: null,
  bio: null,
  created_at: "2026-10-01T00:00:00",
};

function task(id: string, overrides: Partial<MyTaskRecord> = {}): MyTaskRecord {
  return {
    id,
    type: "image",
    message_id: `message-${id}`,
    status: "completed",
    result: "已有结果记录",
    request_data: "{\"secret\":\"请求正文不应显示\"}",
    progress_message: "任务已完成",
    credit_cost: -3,
    progress: 100,
    created_at: "2026-10-05T00:00:00",
    updated_at: "2026-10-05T00:01:00Z",
    asset_type: "character",
    asset_id: `asset-${id}`,
    asset_name: "<b>林岚</b>",
    chapter_title: "雾港来信 · 第 1 章",
    chapter_id: { legacy: true },
    frame_index: 2,
    frame_count: 3,
    frame_text: null,
    ...overrides,
  };
}

function page(pageNumber: number, tasks: MyTaskRecord[], total = tasks.length): MyTaskPage {
  return { total, page: pageNumber, page_size: MY_TASK_PAGE_SIZE, tasks };
}

function servicesFor(
  listMyTasks: WorkspaceServices["listMyTasks"],
): WorkspaceServices {
  return {
    mode: "api",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    restore: async () => user,
    login: async () => user,
    listSeries: async (): Promise<Series[]> => [],
    listMyTeams: async () => [],
    listChapters: async (): Promise<Chapter[]> => [],
    listStoryboardAssets: async (): Promise<StoryboardAsset[]> => [],
    listCharacters: async (): Promise<Character[]> => [],
    listScenes: async (): Promise<Scene[]> => [],
    listProps: async (): Promise<Prop[]> => [],
    getPersonalProductionNotes: async () => { throw new Error("Unexpected notes request."); },
    getPersonalRoughCut: async () => { throw new Error("Unexpected rough-cut request."); },
    listMyTasks,
    logout: vi.fn(),
  };
}

function renderPage(
  services: WorkspaceServices,
  onBack = vi.fn(),
  onUnauthorized = vi.fn(),
) {
  const view = render(
    <MyTasksPage
      onBack={onBack}
      onUnauthorized={onUnauthorized}
      services={services}
      userId={user.id}
    />,
  );
  return { ...view, onBack, onUnauthorized };
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

describe("read-only task page", () => {
  afterEach(() => cleanup());

  it("shows server order and conservative text without rendering request or result media", async () => {
    const first = task("task-first", {
      status: "failed",
      result: "<script>window.bad = true</script>",
      progress: 101,
      asset_name: "<b>林岚</b>",
      chapter_title: "雾港来信 · 第 1 章",
      frame_count: "legacy",
      frame_text: "不应显示的镜头正文",
    });
    const second = task("task-second", { type: "__proto__", status: "constructor", result: "https://example.invalid/output" });
    const listMyTasks = vi.fn(async () => page(1, [first, second], 2));
    const { container } = renderPage(servicesFor(listMyTasks));

    const firstCard = await screen.findByRole("article", { name: "图片，失败" });
    const cards = screen.getAllByRole("article");
    expect(cards[0]).toBe(firstCard);
    expect(within(firstCard).getByText(/<script>window\.bad = true<\/script>/)).toBeInTheDocument();
    expect(screen.getAllByText("<b>林岚</b>")).toHaveLength(2);
    expect(within(firstCard).getByText("雾港来信 · 第 1 章")).toBeInTheDocument();
    expect(within(firstCard).getByText("分镜 2")).toBeInTheDocument();
    expect(within(firstCard).queryByText(/共 .* 帧/)).not.toBeInTheDocument();
    expect(within(firstCard).getByText("记录积分：-3")).toBeInTheDocument();
    expect(within(screen.getByRole("article", { name: "__proto__，状态未识别" })).getByText("已有结果记录")).toBeInTheDocument();
    expect(screen.getByText("状态未识别")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "__proto__" })).toBeInTheDocument();
    expect(screen.queryByText("请求正文不应显示")).not.toBeInTheDocument();
    expect(screen.queryByText("不应显示的镜头正文")).not.toBeInTheDocument();
    expect(container.querySelector("script, img, audio, video, source, iframe, a")).toBeNull();
    expect(within(firstCard).getAllByText(/2026/)).toHaveLength(2);
    expect(listMyTasks).toHaveBeenCalledExactlyOnceWith(1, expect.any(AbortSignal));
  });

  it("offers an explicit return to page one when a later page has become empty", async () => {
    const userEvents = userEvent.setup();
    const listMyTasks = vi.fn(async (pageNumber: number) => (
      pageNumber === 1
        ? page(1, [task("task-first-page")], 11)
        : page(2, [], 0)
    ));
    renderPage(servicesFor(listMyTasks));
    await screen.findByRole("article", { name: "图片，已完成" });

    await userEvents.click(screen.getByRole("button", { name: "下一页" }));
    expect(await screen.findByRole("heading", { name: "本页暂无任务记录" })).toBeInTheDocument();
    expect(listMyTasks).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "回到第一页" })).toBeInTheDocument();

    await userEvents.click(screen.getByRole("button", { name: "回到第一页" }));
    expect(await screen.findByRole("article", { name: "图片，已完成" })).toBeInTheDocument();
    expect(listMyTasks.mock.calls.map(([pageNumber]) => pageNumber)).toEqual([1, 2, 1]);
  });

  it("disables navigation when an explicit reread shrinks totals below the current page", async () => {
    const userEvents = userEvent.setup();
    let pageThreeReads = 0;
    const listMyTasks = vi.fn(async (pageNumber: number) => {
      if (pageNumber === 1) return page(1, [task("page-one", { chapter_title: "第一页记录" })], 30);
      if (pageNumber === 2) return page(2, [task("page-two", { chapter_title: "第二页记录" })], 30);
      pageThreeReads += 1;
      return pageThreeReads === 1
        ? page(3, [task("page-three", { chapter_title: "第三页记录" })], 30)
        : page(3, [], 7);
    });
    renderPage(servicesFor(listMyTasks));

    await screen.findByText("第一页记录");
    await userEvents.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("第二页记录");
    await userEvents.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("第三页记录");
    await userEvents.click(screen.getByRole("button", { name: "重新读取" }));
    await screen.findByRole("heading", { name: "本页暂无任务记录" });

    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "回到第一页" })).toBeEnabled();
    await userEvents.click(screen.getByRole("button", { name: "回到第一页" }));
    expect(await screen.findByText("第一页记录")).toBeInTheDocument();
    expect(listMyTasks.mock.calls.map(([requestedPage]) => requestedPage)).toEqual([1, 2, 3, 3, 1]);
  });

  it("keeps paging available while pending and ignores a late prior-page success", async () => {
    const userEvents = userEvent.setup();
    const firstPage = deferred<MyTaskPage>();
    const staleSecondPage = deferred<MyTaskPage>();
    const currentThirdPage = deferred<MyTaskPage>();
    const listMyTasks = vi.fn((pageNumber: number) => {
      if (pageNumber === 1) return firstPage.promise;
      if (pageNumber === 2) return staleSecondPage.promise;
      return currentThirdPage.promise;
    });
    renderPage(servicesFor(listMyTasks));

    await act(async () => firstPage.resolve(page(1, [task("page-one", { chapter_title: "第一页记录" })], 30)));
    await screen.findByRole("article", { name: "图片，已完成" });
    await userEvents.click(screen.getByRole("button", { name: "下一页" }));
    expect(await screen.findByText("正在读取第 2 / 3 页")).toBeInTheDocument();
    await userEvents.click(screen.getByRole("button", { name: "下一页" }));
    expect(listMyTasks.mock.calls.map(([pageNumber]) => pageNumber)).toEqual([1, 2, 3]);

    await act(async () => currentThirdPage.resolve(page(3, [task("page-three", { chapter_title: "第三页当前记录" })], 30)));
    expect(await screen.findByText("第三页当前记录")).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "图片，已完成" })).toBeInTheDocument();
    expect(screen.getByText("第 3 页")).toBeInTheDocument();

    await act(async () => staleSecondPage.resolve(page(2, [task("stale-page-two", { chapter_title: "过期的第二页" })], 30)));
    await waitFor(() => expect(screen.queryByText("过期的第二页")).not.toBeInTheDocument());
    expect(screen.getByText("第 3 页")).toBeInTheDocument();
  });

  it("allows a pending reread to replace the request and ignores a late 401", async () => {
    const userEvents = userEvent.setup();
    const initial = deferred<MyTaskPage>();
    const staleUnauthorized = deferred<MyTaskPage>();
    const current = deferred<MyTaskPage>();
    const listMyTasks = vi.fn()
      .mockReturnValueOnce(initial.promise)
      .mockReturnValueOnce(staleUnauthorized.promise)
      .mockReturnValueOnce(current.promise);
    const { onUnauthorized } = renderPage(servicesFor(listMyTasks));

    await act(async () => initial.resolve(page(1, [task("initial")], 1)));
    await screen.findByRole("article", { name: "图片，已完成" });
    await userEvents.click(screen.getByRole("button", { name: "重新读取" }));
    await userEvents.click(screen.getByRole("button", { name: "重新读取" }));
    expect(listMyTasks).toHaveBeenCalledTimes(3);

    await act(async () => current.resolve(page(1, [task("current")], 1)));
    expect(await screen.findByRole("heading", { name: "图片" })).toBeInTheDocument();
    await act(async () => staleUnauthorized.reject(new ApiError("http", "expired", 401)));
    await waitFor(() => expect(onUnauthorized).not.toHaveBeenCalled());
    expect(screen.getByRole("heading", { name: "我的任务" })).toBeInTheDocument();
  });

  it("logs out only for a current 401 and keeps the session after 403 until reread succeeds", async () => {
    const userEvents = userEvent.setup();
    const unauthorized = vi.fn();
    const unauthorizedServices = servicesFor(vi.fn(async () => {
      throw new ApiError("http", "expired", 401);
    }));
    renderPage(unauthorizedServices, vi.fn(), unauthorized);
    await waitFor(() => expect(unauthorized).toHaveBeenCalledTimes(1));

    cleanup();
    const listMyTasks = vi.fn()
      .mockRejectedValueOnce(new ApiError("http", "当前账号没有访问权限。", 403, "当前账号没有访问权限。"))
      .mockResolvedValueOnce(page(1, [task("after-reread")], 1));
    const onUnauthorized = vi.fn();
    renderPage(servicesFor(listMyTasks), vi.fn(), onUnauthorized);
    expect(await screen.findByRole("alert")).toHaveTextContent("没有访问权限");
    expect(onUnauthorized).not.toHaveBeenCalled();

    await userEvents.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByRole("article", { name: "图片，已完成" })).toBeInTheDocument();
    expect(listMyTasks).toHaveBeenCalledTimes(2);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});
