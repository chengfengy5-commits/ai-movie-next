import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter, Character, Prop, Scene, Series, StoryboardAsset, User } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { WorkspaceServices } from "../../shared/api/services";
import { MY_TASK_PAGE_SIZE, type MyTaskPage, type MyTaskRecord } from "../../shared/api/myTasks";
import {
  parseTaskReceipt,
  unsupportedTaskHistoryTruth,
  type TaskHistoryTruth,
  type TaskReceipt,
  type TaskRequestRecord,
} from "../../shared/api/taskDetails";
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
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("observes active task progress serially, keeps the open detail stable, and stops at terminal status", async () => {
    vi.useFakeTimers();
    const queued = task("task-active", { status: "queued", progress: 8, progress_message: "等待处理" });
    const processing = task("task-active", { status: "processing", progress: 47, progress_message: "已完成 4 / 10 项" });
    const completed = task("task-active", { status: "completed", progress: 100, progress_message: "已完成" });
    const pendingPoll = deferred<MyTaskPage>();
    const listMyTasks = vi.fn()
      .mockResolvedValueOnce(page(1, [queued]))
      .mockReturnValueOnce(pendingPoll.promise)
      .mockResolvedValueOnce(page(1, [completed]));
    const getTaskReceipt = vi.fn(async (taskId: string) => detailReceipt(taskId, "详情独立来源"));
    renderPage(servicesWithDetails(listMyTasks, { getTaskReceipt }));

    await act(async () => { await Promise.resolve(); });
    const originalCard = screen.getByRole("article", { name: "图片，排队中" });
    fireEvent.click(screen.getByRole("button", { name: "查看详情：图片" }));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByText("详情独立来源")).toBeInTheDocument();
    expect(getTaskReceipt).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(listMyTasks).toHaveBeenCalledTimes(2);
    const pollSignal = listMyTasks.mock.calls[1]?.[1];
    expect(pollSignal).toBeInstanceOf(AbortSignal);
    await act(async () => { await vi.advanceTimersByTimeAsync(9_000); });
    expect(listMyTasks).toHaveBeenCalledTimes(2);

    await act(async () => {
      pendingPoll.resolve(page(1, [processing]));
      await pendingPoll.promise;
    });
    const processingCard = screen.getByRole("article", { name: "图片，处理中" });
    expect(processingCard).toBe(originalCard);
    expect(within(processingCard).getByText("已完成 4 / 10 项")).toBeInTheDocument();
    expect(screen.getByText("详情独立来源")).toBeInTheDocument();
    expect(getTaskReceipt).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(screen.getByRole("article", { name: "图片，已完成" })).toBe(originalCard);
    await act(async () => { await vi.advanceTimersByTimeAsync(9_000); });
    expect(listMyTasks).toHaveBeenCalledTimes(3);
    expect(getTaskReceipt).toHaveBeenCalledTimes(1);
  });

  it("keeps callback-only rerenders from starting a competing list read", async () => {
    vi.useFakeTimers();
    const pendingPoll = deferred<MyTaskPage>();
    const listMyTasks = vi.fn()
      .mockResolvedValueOnce(page(1, [task("callback-owner", { status: "processing" })]))
      .mockReturnValueOnce(pendingPoll.promise);
    const firstUnauthorized = vi.fn();
    const latestUnauthorized = vi.fn();
    const services = servicesFor(listMyTasks);
    const onBack = vi.fn();
    const view = render(
      <MyTasksPage
        onBack={onBack}
        onUnauthorized={firstUnauthorized}
        services={services}
        userId={user.id}
      />,
    );

    await act(async () => { await Promise.resolve(); });
    view.rerender(
      <MyTasksPage
        onBack={onBack}
        onUnauthorized={latestUnauthorized}
        services={services}
        userId={user.id}
      />,
    );
    expect(listMyTasks).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(listMyTasks).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(9_000); });
    expect(listMyTasks).toHaveBeenCalledTimes(2);

    await act(async () => {
      pendingPoll.reject(new ApiError("http", "登录失效", 401));
      await Promise.resolve();
    });
    expect(firstUnauthorized).not.toHaveBeenCalled();
    expect(latestUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("lets manual reread supersede an in-flight observation and ignores its late success", async () => {
    vi.useFakeTimers();
    const pendingPoll = deferred<MyTaskPage>();
    const pendingManualRead = deferred<MyTaskPage>();
    const listMyTasks = vi.fn()
      .mockResolvedValueOnce(page(1, [task("manual-race", { status: "queued", chapter_title: "初始快照" })]))
      .mockReturnValueOnce(pendingPoll.promise)
      .mockReturnValueOnce(pendingManualRead.promise);
    renderPage(servicesFor(listMyTasks));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(listMyTasks).toHaveBeenCalledTimes(2);
    const staleSignal = listMyTasks.mock.calls[1]?.[1];

    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    await act(async () => { await Promise.resolve(); });
    expect(listMyTasks).toHaveBeenCalledTimes(3);
    expect(staleSignal?.aborted).toBe(true);
    await act(async () => {
      pendingManualRead.resolve(page(1, [task("manual-race", { status: "processing", chapter_title: "手动新快照" })]));
      await pendingManualRead.promise;
    });
    expect(screen.getByText("手动新快照")).toBeInTheDocument();

    await act(async () => {
      pendingPoll.resolve(page(1, [task("manual-race", { status: "completed", chapter_title: "过期自动快照" })]));
      await pendingPoll.promise;
    });
    expect(screen.getByText("手动新快照")).toBeInTheDocument();
    expect(screen.queryByText("过期自动快照")).not.toBeInTheDocument();
  });

  it("keeps page and user changes isolated from a late poll and handles a current polling 401", async () => {
    vi.useFakeTimers();
    const stalePoll = deferred<MyTaskPage>();
    const oldList = vi.fn()
      .mockResolvedValueOnce(page(1, [task("old-owner", { status: "queued", chapter_title: "旧账号任务" })], 20))
      .mockReturnValueOnce(stalePoll.promise)
      .mockResolvedValueOnce(page(2, [task("page-two", { status: "processing", chapter_title: "第二页任务" })], 20));
    const onUnauthorized = vi.fn();
    const oldServices = servicesFor(oldList);
    const view = renderPage(oldServices, vi.fn(), onUnauthorized);
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(oldList).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await act(async () => { await Promise.resolve(); });
    expect(oldList).toHaveBeenCalledTimes(3);
    expect(screen.getByText("第二页任务")).toBeInTheDocument();
    const staleSignal = oldList.mock.calls[1]?.[1];
    expect(staleSignal?.aborted).toBe(true);
    await act(async () => {
      stalePoll.reject(new ApiError("http", "旧账号登录失效", 401));
      await Promise.resolve();
    });
    expect(onUnauthorized).not.toHaveBeenCalled();

    const newInitial = deferred<MyTaskPage>();
    const newList = vi.fn()
      .mockReturnValueOnce(newInitial.promise)
      .mockRejectedValueOnce(new ApiError("http", "当前账号登录失效", 401));
    const newServices = servicesFor(newList);
    view.rerender(
      <MyTasksPage services={newServices} userId="next-user" onBack={vi.fn()} onUnauthorized={onUnauthorized} />,
    );
    expect(screen.queryByText("第二页任务")).not.toBeInTheDocument();
    await act(async () => {
      newInitial.resolve(page(2, [task("new-owner", { status: "processing", chapter_title: "新账号任务" })], 20));
      await newInitial.promise;
    });
    expect(screen.getByText("新账号任务")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(newList).toHaveBeenCalledTimes(2);
  });

  it("retains the last snapshot through bounded observation failures and resumes by manual reread", async () => {
    vi.useFakeTimers();
    const active = task("task-retry-observation", { status: "processing", chapter_title: "保留的任务快照" });
    const listMyTasks = vi.fn()
      .mockResolvedValueOnce(page(1, [active]))
      .mockRejectedValueOnce(new ApiError("network", "暂时断网"))
      .mockRejectedValueOnce(new ApiError("network", "仍然断网"))
      .mockRejectedValueOnce(new ApiError("network", "连续断网"))
      .mockResolvedValueOnce(page(1, [task("task-retry-observation", { status: "completed", chapter_title: "恢复后的任务快照" })]));
    renderPage(servicesFor(listMyTasks));
    await act(async () => { await Promise.resolve(); });
    const originalCard = screen.getByRole("article", { name: "图片，处理中" });

    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(screen.getByRole("status")).toHaveTextContent("1/3");
    expect(screen.getByText("保留的任务快照")).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "图片，处理中" })).toBe(originalCard);
    await act(async () => { await vi.advanceTimersByTimeAsync(5_999); });
    expect(listMyTasks).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(screen.getByRole("status")).toHaveTextContent("2/3");
    await act(async () => { await vi.advanceTimersByTimeAsync(11_999); });
    expect(listMyTasks).toHaveBeenCalledTimes(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(screen.getByRole("status")).toHaveTextContent("连续失败 3 次");
    expect(screen.getByText("保留的任务快照")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(listMyTasks).toHaveBeenCalledTimes(4);

    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText("恢复后的任务快照")).toBeInTheDocument();
    expect(screen.queryByText(/连续失败 3 次/)).not.toBeInTheDocument();
    expect(listMyTasks).toHaveBeenCalledTimes(5);
  });

  it("pauses hidden observation, resumes when visible, and aborts an unmounted read", async () => {
    vi.useFakeTimers();
    const pendingPoll = deferred<MyTaskPage>();
    const onUnauthorized = vi.fn();
    const listMyTasks = vi.fn()
      .mockResolvedValueOnce(page(1, [task("visibility-task", { status: "processing" })]))
      .mockReturnValueOnce(pendingPoll.promise);
    const view = renderPage(servicesFor(listMyTasks), vi.fn(), onUnauthorized);
    await act(async () => { await Promise.resolve(); });
    const originalHidden = Object.getOwnPropertyDescriptor(document, "hidden");
    const setHidden = (hidden: boolean) => {
      Object.defineProperty(document, "hidden", { configurable: true, value: hidden });
      fireEvent(document, new Event("visibilitychange"));
    };

    try {
      setHidden(true);
      await act(async () => { await vi.advanceTimersByTimeAsync(6_000); });
      expect(listMyTasks).toHaveBeenCalledTimes(1);

      setHidden(false);
      await act(async () => { await vi.advanceTimersByTimeAsync(2_999); });
      expect(listMyTasks).toHaveBeenCalledTimes(1);
      await act(async () => { await vi.advanceTimersByTimeAsync(1); });
      expect(listMyTasks).toHaveBeenCalledTimes(2);
      const pollSignal = listMyTasks.mock.calls[1]?.[1];
      expect(pollSignal).toBeInstanceOf(AbortSignal);

      view.unmount();
      expect(pollSignal?.aborted).toBe(true);
      await act(async () => {
        pendingPoll.reject(new ApiError("http", "登录失效", 401));
        await Promise.resolve();
      });
      expect(onUnauthorized).not.toHaveBeenCalled();
    } finally {
      if (originalHidden === undefined) Reflect.deleteProperty(document, "hidden");
      else Object.defineProperty(document, "hidden", originalHidden);
    }
  });

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

function detailReceipt(
  id: string,
  result: unknown = "完整结果全文",
  historyTruth: TaskHistoryTruth = unsupportedTaskHistoryTruth(),
): TaskReceipt {
  return {
    id,
    type: "storyboard",
    messageId: "message-" + id,
    status: "completed",
    result,
    billingStatus: "unbilled",
    quotedAmount: 0,
    progress: 100,
    progressMessage: "全部完成",
    externalTaskId: null,
    externalProvider: null,
    createdAt: "2026-10-15T08:00:00Z",
    updatedAt: "2026-10-15T08:01:00Z",
    historyTruth,
  };
}

function detailRequest(id: string, request: unknown = { prompt: "saved request" }): TaskRequestRecord {
  return {
    id,
    type: "storyboard",
    status: "completed",
    modelName: "model-record",
    creditCost: 0,
    externalTaskId: null,
    externalProvider: null,
    createdAt: "2026-10-15T08:00:00Z",
    request,
  };
}

function servicesWithDetails(
  listMyTasks: WorkspaceServices["listMyTasks"],
  overrides: {
    getTaskReceipt?: WorkspaceServices["getTaskReceipt"];
    getTaskRequest?: WorkspaceServices["getTaskRequest"];
  } = {},
): WorkspaceServices {
  return {
    ...servicesFor(listMyTasks),
    getTaskReceipt: overrides.getTaskReceipt ?? (async (taskId) => detailReceipt(taskId)),
    getTaskRequest: overrides.getTaskRequest ?? (async (taskId) => detailRequest(taskId)),
  };
}

describe("my task details", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("fetches full records only after opening, sanitizes text, and returns keyboard focus", async () => {
    const userEvents = userEvent.setup();
    const clipboard = vi.fn(async (_value: string) => undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: clipboard },
    });
    const listMyTasks = vi.fn(async () => page(1, [task("task-one", { type: "storyboard" })]));
    const getTaskReceipt = vi.fn(async (taskId: string) => detailReceipt(taskId, JSON.stringify({
      frames: [{ position: 1, text: "完整分镜文字，列表预览已截断" }],
      remote: "https://cdn.example.test/result.mp4?X-Amz-Signature=private-signature",
      literal: "<img src=x onerror=alert(1)>",
    })));
    const getTaskRequest = vi.fn(async (taskId: string) => detailRequest(taskId, {
      api_key: "private-api-key",
      prompt: "安全请求文字",
    }));
    const currentServices = servicesWithDetails(listMyTasks, { getTaskReceipt, getTaskRequest });
    const { container } = renderPage(currentServices);

    const opener = await screen.findByRole("button", { name: "查看详情：智能分镜" });
    expect(getTaskReceipt).not.toHaveBeenCalled();
    expect(getTaskRequest).not.toHaveBeenCalled();

    await userEvents.click(opener);
    const result = await screen.findByLabelText("安全结果文本");
    const request = await screen.findByLabelText("安全请求文本");
    expect(result.textContent).toContain("完整分镜文字，列表预览已截断");
    expect(request.textContent).toContain("安全请求文字");
    expect(result.textContent).toContain("<img src=x onerror=alert(1)>");
    expect(result.textContent).not.toContain("private-signature");
    expect(request.textContent).not.toContain("private-api-key");
    expect(container.querySelector("img, video, audio, a, script")).toBeNull();
    expect(getTaskReceipt).toHaveBeenCalledExactlyOnceWith("task-one", expect.any(AbortSignal));
    expect(getTaskRequest).toHaveBeenCalledExactlyOnceWith("task-one", expect.any(AbortSignal));
    expect(within(screen.getByRole("region", { name: "结果与费用记录" })).getByText("0 积分")).toBeInTheDocument();
    expect(screen.getByText(/不能据此认定实际扣费/)).toBeInTheDocument();

    await userEvents.click(screen.getByRole("button", { name: "复制安全结果文本" }));
    await waitFor(() => expect(clipboard).toHaveBeenCalledTimes(1));
    expect(clipboard.mock.calls[0]?.[0]).toBe(result.textContent);
    expect(clipboard.mock.calls[0]?.[0]).not.toContain("private-signature");

    await userEvents.keyboard("{Escape}");
    await waitFor(() => expect(document.activeElement).toBe(opener));
  });

  it("renders parsed history evidence in the mounted task detail without inferring provider failure", async () => {
    const userEvents = userEvent.setup();
    const wireReceipt = {
      id: "task-one",
      type: "image.single",
      message_id: "message-task-one",
      status: "failed",
      result: "legacy result",
      billing_status: "unbilled",
      quoted_amount: 0,
      progress: 100,
      progress_message: null,
      external_task_id: null,
      external_provider: null,
      created_at: "2026-10-15T08:00:00Z",
      updated_at: "2026-10-15T08:01:00Z",
      history_truth: {
        version: 1,
        source: {
          status: "unknown",
          source: "billing_units.operation_and_step_graph_version",
          unit_count: 1,
          registered_unit_count: 1,
          operations: ["image.single"],
          conflicts: ["intent_graph_mismatch"],
        },
        worker: {
          status: "expired_lease",
          source: "ai_tasks.worker_lease_and_recovery_status",
          recovery_status: "reconciling",
          freshness: "lease_expired",
          lease_until: "2026-10-15T07:00:00Z",
          task_updated_at: "2026-10-15T07:01:00Z",
          evaluated_at: "2026-10-15T08:00:00Z",
        },
        provider: {
          status: "unknown",
          source: "external_submissions_and_linked_result_evidence",
          unit_count: 1,
          verified_unit_count: 0,
          known_failure_unit_count: 0,
          unknown_unit_count: 1,
          unsupported_unit_count: 0,
          units: [{
            operation: "image.single",
            graph_version: "image-single-v1",
            status: "unknown",
            unit_result_status: "failed",
            step_statuses: ["failed"],
            submission_statuses: ["failed"],
            providers: ["openai"],
            provider_evidence_count: 0,
            timing: "unknown",
            timely_evidence_count: 0,
            late_evidence_count: 0,
            unknown_timing_count: 0,
            observed_at: null,
            has_unverified_failed_submission: true,
          }],
        },
        local_disposition: {
          status: "observed",
          source_coverage: ["ai_tasks", "billing_units"],
          unit_count: 1,
          observations: [{
            status: "deadline_elapsed_unsettled",
            source: "billing_units.deadline_at_and_result_status",
            recorded_at: "2026-10-15T07:00:00Z",
            reason: null,
            phase: null,
          }],
        },
        billing_scope: {
          unit_count: 1,
          legacy_quote_scope: "single_unit",
          aggregated: false,
        },
      },
    };
    const listMyTasks = vi.fn(async () => page(1, [task("task-one")]));
    const getTaskReceipt = vi.fn(async (taskId: string) => parseTaskReceipt(wireReceipt, taskId));
    renderPage(servicesWithDetails(listMyTasks, { getTaskReceipt }));

    await userEvents.click(await screen.findByRole("button", { name: "查看详情：图片" }));

    expect(await screen.findByText("任务来源未知 · 1 个计费单元")).toBeInTheDocument();
    expect(screen.getByText("已接受提交意图与计费单元任务图不一致")).toBeInTheDocument();
    expect(screen.getByText("Worker 租约已过期")).toBeInTheDocument();
    expect(screen.getByText("Provider 结果未知 · 覆盖 0/1 个单元")).toBeInTheDocument();
    expect(screen.getByText(/提交记录为失败，但缺少独立有效的 Provider 失败证据/)).toBeInTheDocument();
    expect(screen.getByText(/执行期限已过，单元仍记录为待处理或未知/)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "结果与费用记录" })).getByText("0 积分")).toBeInTheDocument();
  });

  it("keeps the receipt visible when membership blocks only the request record", async () => {
    const userEvents = userEvent.setup();
    const onUnauthorized = vi.fn();
    const listMyTasks = vi.fn(async () => page(1, [task("task-one")]));
    const currentServices = servicesWithDetails(listMyTasks, {
      getTaskReceipt: async (taskId) => detailReceipt(taskId, "结果仍可读取"),
      getTaskRequest: async () => {
        throw new ApiError("http", "需要会员", 403, "会员资格暂不可用");
      },
    });
    renderPage(currentServices, vi.fn(), onUnauthorized);

    await userEvents.click(await screen.findByRole("button", { name: "查看详情：图片" }));
    expect(await screen.findByText("结果仍可读取")).toBeInTheDocument();
    expect(await screen.findByText(/暂不可查看任务请求记录/)).toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("retains and marks the prior receipt after a refresh fails", async () => {
    const userEvents = userEvent.setup();
    const listMyTasks = vi.fn(async () => page(1, [task("task-one")]));
    const getTaskReceipt = vi.fn()
      .mockResolvedValueOnce(detailReceipt("task-one", "已读取的结果快照"))
      .mockRejectedValueOnce(new ApiError("network", "暂时断网"));
    const currentServices = servicesWithDetails(listMyTasks, { getTaskReceipt });
    renderPage(currentServices);

    await userEvents.click(await screen.findByRole("button", { name: "查看详情：图片" }));
    expect(await screen.findByText("已读取的结果快照")).toBeInTheDocument();
    await userEvents.click(screen.getByRole("button", { name: "刷新结果记录" }));
    expect(await screen.findByText("下方仍保留上次读取的旧快照。")).toBeInTheDocument();
    expect(screen.getByText("已读取的结果快照")).toBeInTheDocument();
    expect(getTaskReceipt).toHaveBeenCalledTimes(2);
  });

  it("aborts reads on close and ignores a late 401 from the closed task", async () => {
    const userEvents = userEvent.setup();
    const lateRequest = deferred<TaskRequestRecord>();
    const onUnauthorized = vi.fn();
    const first = task("task-one", { type: "storyboard" });
    const second = task("task-two", { type: "storyboard" });
    const listMyTasks = vi.fn(async () => page(1, [first, second]));
    const getTaskReceipt = vi.fn(async (taskId: string) => detailReceipt(taskId, "result-" + taskId));
    const getTaskRequest = vi.fn((taskId: string, _signal: AbortSignal) => (
      taskId === "task-one" ? lateRequest.promise : Promise.resolve(detailRequest(taskId))
    ));
    const currentServices = servicesWithDetails(listMyTasks, { getTaskReceipt, getTaskRequest });
    renderPage(currentServices, vi.fn(), onUnauthorized);

    const detailButtons = await screen.findAllByRole("button", { name: "查看详情：智能分镜" });
    await userEvents.click(detailButtons[0]!);
    const firstRequestSignal = getTaskRequest.mock.calls[0]?.[1];
    expect(firstRequestSignal).toBeInstanceOf(AbortSignal);

    await userEvents.keyboard("{Escape}");
    await waitFor(() => expect(firstRequestSignal?.aborted).toBe(true));
    await act(async () => {
      lateRequest.reject(new ApiError("http", "登录失效", 401));
      await Promise.resolve();
    });

    await userEvents.click(screen.getAllByRole("button", { name: "查看详情：智能分镜" })[1]!);
    expect(await screen.findByText("result-task-two")).toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("aborts a receipt refresh on close and ignores its late 401", async () => {
    const userEvents = userEvent.setup();
    const lateReceipt = deferred<TaskReceipt>();
    const onUnauthorized = vi.fn();
    const listMyTasks = vi.fn(async () => page(1, [task("task-one")]));
    const getTaskReceipt = vi.fn(
      (_taskId: string, _signal: AbortSignal): Promise<TaskReceipt> => (
        Promise.resolve(detailReceipt("task-one", "已读取的结果快照"))
      ),
    ).mockImplementationOnce(() => Promise.resolve(detailReceipt("task-one", "已读取的结果快照")))
      .mockImplementationOnce((_taskId, _signal) => lateReceipt.promise);
    const currentServices = servicesWithDetails(listMyTasks, { getTaskReceipt });
    renderPage(currentServices, vi.fn(), onUnauthorized);

    await userEvents.click(await screen.findByRole("button", { name: "查看详情：图片" }));
    expect(await screen.findByText("已读取的结果快照")).toBeInTheDocument();
    await userEvents.click(screen.getByRole("button", { name: "刷新结果记录" }));
    await waitFor(() => expect(getTaskReceipt).toHaveBeenCalledTimes(2));
    const refreshSignal = getTaskReceipt.mock.calls[1]?.[1];
    expect(refreshSignal).toBeInstanceOf(AbortSignal);

    await userEvents.keyboard("{Escape}");
    await waitFor(() => expect(refreshSignal?.aborted).toBe(true));
    await act(async () => {
      lateReceipt.reject(new ApiError("http", "登录失效", 401));
      await Promise.resolve();
    });
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("aborts a request refresh and ignores its late 401 after owner changes", async () => {
    const userEvents = userEvent.setup();
    const lateRequest = deferred<TaskRequestRecord>();
    const onUnauthorized = vi.fn();
    const firstList = vi.fn(async () => page(1, [task("task-one")]));
    const getTaskRequest = vi.fn(
      (_taskId: string, _signal: AbortSignal): Promise<TaskRequestRecord> => (
        Promise.resolve(detailRequest("task-one"))
      ),
    ).mockImplementationOnce(() => Promise.resolve(detailRequest("task-one")))
      .mockImplementationOnce((_taskId, _signal) => lateRequest.promise);
    const firstServices = servicesWithDetails(firstList, { getTaskRequest });
    const secondList = vi.fn(async () => page(1, [task("task-one", { asset_name: "新账号任务" })]));
    const secondServices = servicesWithDetails(secondList);
    const onBack = vi.fn();
    const view = render(
      <MyTasksPage onBack={onBack} onUnauthorized={onUnauthorized} services={firstServices} userId={user.id} />,
    );

    await userEvents.click(await screen.findByRole("button", { name: "查看详情：图片" }));
    expect((await screen.findByLabelText("安全请求文本")).textContent).toContain("saved request");
    await userEvents.click(screen.getByRole("button", { name: "刷新请求记录" }));
    await waitFor(() => expect(getTaskRequest).toHaveBeenCalledTimes(2));
    const refreshSignal = getTaskRequest.mock.calls[1]?.[1];
    expect(refreshSignal).toBeInstanceOf(AbortSignal);

    view.rerender(
      <MyTasksPage onBack={onBack} onUnauthorized={onUnauthorized} services={secondServices} userId="next-user" />,
    );
    await waitFor(() => expect(refreshSignal?.aborted).toBe(true));
    expect(await screen.findByText("新账号任务")).toBeInTheDocument();
    await act(async () => {
      lateRequest.reject(new ApiError("http", "登录失效", 401));
      await Promise.resolve();
    });
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("exits the current session for an active task-detail 401", async () => {
    const onUnauthorized = vi.fn();
    const listMyTasks = vi.fn(async () => page(1, [task("task-one")]));
    const currentServices = servicesWithDetails(listMyTasks, {
      getTaskReceipt: async () => {
        throw new ApiError("http", "登录状态已失效", 401);
      },
    });
    const userEvents = userEvent.setup();
    renderPage(currentServices, vi.fn(), onUnauthorized);

    await userEvents.click(await screen.findByRole("button", { name: "查看详情：图片" }));
    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
  });
});
