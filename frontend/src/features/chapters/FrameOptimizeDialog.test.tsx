import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter, Series, StoryboardAsset } from "../../shared/api/contracts";
import type { CreatorModel, CreatorSettingsServicePort } from "../../shared/api/creatorSettings";
import { ApiError } from "../../shared/api/errors";
import type {
  FrameOptimizeCapabilities,
  FrameOptimizeServicePort,
  FrameOptimizeSubmitInput,
  FrameOptimizeTaskStatus,
} from "../../shared/api/frameOptimize";
import type { WorkspaceServices } from "../../shared/api/services";
import type { FrameOptimizeSourceCheck } from "./FrameOptimizeDialog";
import { FrameOptimizeDialog } from "./FrameOptimizeDialog";

const chapter: Chapter = {
  id: "chapter-dialog-test",
  series_id: "series-1",
  title: "第 1 集",
  content: [{ text: "原始镜头文字", storyboard: ["storyboard-1"] }],
  order: 1,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
  lock: null,
};

const asset: StoryboardAsset = {
  id: "storyboard-1",
  series_id: "series-1",
  chapter_id: chapter.id,
  frame_index: 0,
  name: "镜头素材",
  description: null,
  image_url: null,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
};

const series: Pick<Series, "style_prompt_id" | "style_prompt"> = {
  style_prompt_id: null,
  style_prompt: null,
};

const model: CreatorModel = {
  id: "model-1",
  category: "text",
  name: "文本模型",
  provider: "openai",
  params: null,
  sort_order: 1,
  is_system: true,
  user_id: "system",
};

const capabilities: FrameOptimizeCapabilities = {
  operation: "frame.optimize",
  enabled: true,
  reason: null,
  supported_providers: ["openai"],
};

const registeredRequest: FrameOptimizeSubmitInput = {
  chapter_id: chapter.id,
  model_id: model.id,
  prompt_id: "",
  enable_thinking: false,
  concurrency: 5,
  chapter_texts: ["【剧情片段】\n原始镜头文字"],
  frames: [{ index: 0, text: "原始镜头文字", asset_context: "" }],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => {
    resolve = complete;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function captureButtonHandler(button: HTMLElement): () => void {
  const propsKey = Object.getOwnPropertyNames(button).find((key) => key.startsWith("__reactProps$"));
  if (propsKey === undefined) throw new Error("The rendered button should expose its React click handler.");
  const props = (button as unknown as Record<string, unknown>)[propsKey];
  if (typeof props !== "object" || props === null || !("onClick" in props) || typeof props.onClick !== "function") {
    throw new Error("The rendered button does not have a click handler.");
  }
  const onClick = props.onClick as (event: unknown) => void;
  return () => onClick({});
}

const partialStatus: FrameOptimizeTaskStatus = {
  task_id: "optimize-task-1",
  status: "failed",
  progress: 100,
  progress_message: "一个镜头已应用",
  result: { partial: [{ index: 0, optimized_text: "优化后的镜头文字" }], error: "one_item_failed" },
};

function makeServices(overrides: Partial<FrameOptimizeServicePort> = {}) {
  const creatorSettings: Pick<CreatorSettingsServicePort, "listModels" | "listPrompts"> = {
    listModels: vi.fn(async () => [model]),
    listPrompts: vi.fn(async () => []),
  };
  const frameOptimize: FrameOptimizeServicePort = {
    getCapabilities: vi.fn(async () => capabilities),
    submit: vi.fn(async () => ({ task_id: "optimize-task-1", chapter_id: chapter.id, frame_count: 1 })),
    getRunning: vi.fn(async () => null),
    getTaskStatus: vi.fn(async () => partialStatus),
    cancel: vi.fn(async () => ({ task_id: "optimize-task-1", status: "cancelling" as const })),
    ...overrides,
  };
  const services = {
    frameOptimize,
    creatorSettings,
    getTaskRequest: vi.fn(async (taskId: string) => ({
      id: taskId,
      type: "batch-optimize",
      status: "processing",
      modelName: null,
      creditCost: null,
      externalTaskId: null,
      externalProvider: null,
      createdAt: null,
      request: registeredRequest,
    })),
  } as unknown as WorkspaceServices;
  return { frameOptimize, services };
}

function renderDialog(
  services: WorkspaceServices,
  options: {
    current?: () => boolean;
    verify?: (status: FrameOptimizeTaskStatus, request: FrameOptimizeSubmitInput | null) => Promise<FrameOptimizeSourceCheck>;
    prepare?: () => Promise<{ chapter: Chapter; assets: StoryboardAsset[]; names: { characters: []; scenes: []; props: []; prompts: [] } }>;
  } = {},
  userId = "user-1",
) {
  const onClose = vi.fn();
  const verify = options.verify ?? vi.fn(async () => ({ status: "verified" as const, message: "章节源数据已核对。" }));
  const prepare = options.prepare ?? vi.fn(async () => ({
    chapter,
    assets: [asset],
    names: { characters: [], scenes: [], props: [], prompts: [] },
  }));
  const view = render(
    <FrameOptimizeDialog
      assets={[asset]}
      chapter={chapter}
      isCurrent={options.current ?? (() => true)}
      onClose={onClose}
      onUnauthorized={vi.fn()}
      prepareSubmit={prepare}
      restoreOriginals={vi.fn(async () => ({ status: "verified" as const, message: "原文已核对。" }))}
      series={series}
      seriesId="series-1"
      seriesName="剧集"
      services={services}
      userId={userId}
      verifyTaskSource={verify}
    />,
  );
  return { ...view, onClose, verify, prepare };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("FrameOptimizeDialog", () => {
  it("does not offer original restore until the first running-task lookup confirms an empty chapter", async () => {
    const user = userEvent.setup();
    const pendingRunning = deferred<null>();
    const restoreChapter: Chapter = {
      ...chapter,
      content: [{ text: "优化文字", original_text: "原文", storyboard: [asset.id] }],
    };
    const { services } = makeServices({ getRunning: vi.fn(() => pendingRunning.promise) });
    render(
      <FrameOptimizeDialog
        assets={[asset]}
        chapter={restoreChapter}
        isCurrent={() => true}
        onClose={vi.fn()}
        onUnauthorized={vi.fn()}
        prepareSubmit={async () => ({ chapter: restoreChapter, assets: [asset], names: { characters: [], scenes: [], props: [], prompts: [] } })}
        restoreOriginals={vi.fn(async () => ({ status: "verified" as const, message: "已恢复" }))}
        series={series}
        seriesId="series-1"
        seriesName="剧集"
        services={services}
        userId="user-loading-restore"
        verifyTaskSource={async () => ({ status: "verified", message: "已核对" })}
      />,
    );
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    await user.click(dialog.getByRole("checkbox", { name: "镜头 1 · 已有原文" }));
    expect(dialog.queryByRole("button", { name: "恢复所选镜头原文" })).not.toBeInTheDocument();
    await act(async () => pendingRunning.resolve(null));
    expect(await dialog.findByRole("button", { name: "恢复所选镜头原文" })).toBeEnabled();
  });

  it("keeps submit and restore disabled when the running-task lookup fails", async () => {
    const user = userEvent.setup();
    const restoreChapter: Chapter = {
      ...chapter,
      content: [{ text: "优化文字", original_text: "原文", storyboard: [asset.id] }],
    };
    const { services } = makeServices({
      getRunning: vi.fn(async () => { throw new ApiError("network", "offline"); }),
    });
    render(
      <FrameOptimizeDialog
        assets={[asset]}
        chapter={restoreChapter}
        isCurrent={() => true}
        onClose={vi.fn()}
        onUnauthorized={vi.fn()}
        prepareSubmit={async () => ({ chapter: restoreChapter, assets: [asset], names: { characters: [], scenes: [], props: [], prompts: [] } })}
        restoreOriginals={vi.fn(async () => ({ status: "verified" as const, message: "已恢复" }))}
        series={series}
        seriesId="series-1"
        seriesName="剧集"
        services={services}
        userId="user-running-lookup-failed"
        verifyTaskSource={async () => ({ status: "verified", message: "已核对" })}
      />,
    );
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    await user.click(dialog.getByRole("checkbox", { name: "镜头 1 · 已有原文" }));
    expect(await dialog.findByText(/暂时无法读取运行任务；在确认本章节没有活任务前，提交与原文恢复均不可用/)).toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "提交优化" })).toBeDisabled();
    expect(dialog.queryByRole("button", { name: "恢复所选镜头原文" })).not.toBeInTheDocument();
  });

  it("explains that restore is unavailable when no real task-query port is wired", async () => {
    const user = userEvent.setup();
    const restoreChapter: Chapter = {
      ...chapter,
      content: [{ text: "优化文字", original_text: "原文", storyboard: [asset.id] }],
    };
    const { services } = makeServices();
    delete (services as unknown as { frameOptimize?: FrameOptimizeServicePort }).frameOptimize;
    render(
      <FrameOptimizeDialog
        assets={[asset]}
        chapter={restoreChapter}
        isCurrent={() => true}
        onClose={vi.fn()}
        onUnauthorized={vi.fn()}
        prepareSubmit={async () => ({ chapter: restoreChapter, assets: [asset], names: { characters: [], scenes: [], props: [], prompts: [] } })}
        restoreOriginals={vi.fn(async () => ({ status: "verified" as const, message: "已恢复" }))}
        series={series}
        seriesId="series-1"
        seriesName="剧集"
        services={services}
        userId="user-no-frame-optimize-port"
        verifyTaskSource={async () => ({ status: "verified", message: "已核对" })}
      />,
    );
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    await user.click(dialog.getByRole("checkbox", { name: "镜头 1 · 已有原文" }));
    expect(await dialog.findByText(/原文恢复均已禁用，因为无法确认本章节没有运行中的任务/)).toBeInTheDocument();
    expect(dialog.queryByRole("button", { name: "恢复所选镜头原文" })).not.toBeInTheDocument();
  });

  it("reserves a chapter synchronously across deferred preflight and sends only one submit", async () => {
    const pendingPrepare = deferred<{ chapter: Chapter; assets: StoryboardAsset[]; names: { characters: []; scenes: []; props: []; prompts: [] } }>();
    const prepare = vi.fn(() => pendingPrepare.promise);
    const { frameOptimize, services } = makeServices();
    renderDialog(services, { prepare }, "user-dialog-double-submit");
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    await userEvent.setup().click(dialog.getByRole("button", { name: "全选可定位分镜" }));
    const invokeSubmit = captureButtonHandler(await dialog.findByRole("button", { name: "提交优化" }));
    act(() => {
      invokeSubmit();
      invokeSubmit();
    });
    expect(prepare).toHaveBeenCalledTimes(1);
    await act(async () => pendingPrepare.resolve({
      chapter,
      assets: [asset],
      names: { characters: [], scenes: [], props: [], prompts: [] },
    }));
    await waitFor(() => expect(frameOptimize.submit).toHaveBeenCalledTimes(1));
  });

  it("submits the selected batch once and verifies partial terminal output against chapter source", async () => {
    const user = userEvent.setup();
    const { frameOptimize, services } = makeServices();
    const verify = vi.fn(async () => ({ status: "verified" as const, message: "章节源数据已核对。" }));
    renderDialog(services, { verify }, "user-dialog-submit");
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    await user.click(dialog.getByRole("button", { name: "全选可定位分镜" }));
    await user.click(dialog.getByRole("button", { name: "提交优化" }));

    expect(await dialog.findByText("终态：失败（可能含部分结果）")).toBeInTheDocument();
    expect(dialog.getByText("章节源数据已核对。")).toBeInTheDocument();
    expect(dialog.getByRole("region", { name: "任务返回的分镜结果" })).toHaveTextContent("镜头 1：优化后的镜头文字");
    expect(dialog.getByText("任务错误：one_item_failed")).toBeInTheDocument();
    expect(frameOptimize.submit).toHaveBeenCalledTimes(1);
    expect(frameOptimize.submit).toHaveBeenCalledWith(registeredRequest, expect.any(AbortSignal));
    expect(verify).toHaveBeenCalledWith(partialStatus, registeredRequest, expect.any(AbortSignal));
  });

  it("keeps an unknown submit frozen after reopening when running lookup returns null", async () => {
    const user = userEvent.setup();
    const { frameOptimize, services } = makeServices({
      submit: vi.fn(async () => { throw new ApiError("network", "connection lost"); }),
    });
    const first = renderDialog(services, {}, "user-dialog-unknown");
    const firstDialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    await user.click(firstDialog.getByRole("button", { name: "全选可定位分镜" }));
    await user.click(firstDialog.getByRole("button", { name: "提交优化" }));
    expect(await firstDialog.findByRole("alert")).toHaveTextContent("提交结果暂时无法确认");
    expect(frameOptimize.submit).toHaveBeenCalledTimes(1);
    first.unmount();

    const reopened = renderDialog(services, {}, "user-dialog-unknown");
    const reopenedDialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    expect(await reopenedDialog.findByRole("alert")).toHaveTextContent("此前提交结果未确认");
    expect(reopenedDialog.queryByRole("button", { name: "提交优化" })).not.toBeInTheDocument();
    expect(frameOptimize.submit).toHaveBeenCalledTimes(1);
    reopened.unmount();
  });

  it("reattaches only after checking task type, chapter, request shape, and current stable frame", async () => {
    const { frameOptimize, services } = makeServices({
      getRunning: vi.fn(async () => ({
        task_id: "optimize-task-1", status: "processing", progress: 35, progress_message: "处理中", created_at: null,
      })),
      getTaskStatus: vi.fn(async () => ({ ...partialStatus, status: "completed" })),
    });
    const getTaskRequest = vi.fn(async (taskId: string) => ({
      id: taskId,
      type: "batch-optimize",
      status: "processing",
      modelName: null,
      creditCost: null,
      externalTaskId: null,
      externalProvider: null,
      createdAt: null,
      request: { ...registeredRequest, prompt_id: null },
    }));
    (services as unknown as { getTaskRequest: typeof getTaskRequest }).getTaskRequest = getTaskRequest;
    renderDialog(services, {}, "user-dialog-reattach");
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    expect(await dialog.findByText(/已核对章节 chapter-dialog-test 的 frame.optimize 登记请求/)).toBeInTheDocument();
    await waitFor(() => expect(frameOptimize.getTaskStatus).toHaveBeenCalledWith("optimize-task-1", expect.any(AbortSignal)));
    expect(frameOptimize.submit).not.toHaveBeenCalled();
  });

  it("keeps reading a verified running task when the current capability is disabled", async () => {
    const { frameOptimize, services } = makeServices({
      getCapabilities: vi.fn(async () => ({
        operation: "frame.optimize" as const,
        enabled: false,
        reason: "worker_unavailable" as const,
        supported_providers: [],
      })),
      getRunning: vi.fn(async () => ({
        task_id: "optimize-task-1", status: "processing", progress: 35, progress_message: "处理中", created_at: null,
      })),
      getTaskStatus: vi.fn(async () => ({ ...partialStatus, status: "completed" })),
    });
    renderDialog(services, {}, "user-dialog-disabled-capability");
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    expect(await dialog.findByText("终态：完成")).toBeInTheDocument();
    expect(dialog.getByText("服务端分镜优化 worker 当前不可用。")).toBeInTheDocument();
    expect(frameOptimize.getTaskStatus).toHaveBeenCalledWith("optimize-task-1", expect.any(AbortSignal));
    expect(dialog.queryByRole("button", { name: "提交优化" })).not.toBeInTheDocument();
    expect(frameOptimize.submit).not.toHaveBeenCalled();
  });

  it("reattaches to a same-account legacy task for read-only observation and cancellation", async () => {
    const pendingStatus = new Promise<FrameOptimizeTaskStatus>(() => undefined);
    const { frameOptimize, services } = makeServices({
      getRunning: vi.fn(async () => ({
        task_id: "legacy-task", status: "processing", progress: 10, progress_message: "legacy", created_at: null,
      })),
      getTaskStatus: vi.fn(() => pendingStatus),
    });
    const getTaskRequest = vi.fn(async (taskId: string) => ({
      id: taskId,
      type: "batch-optimize",
      status: "processing",
      modelName: null,
      creditCost: null,
      externalTaskId: null,
      externalProvider: null,
      createdAt: null,
      request: { chapter_id: chapter.id, frame_count: 1 },
    }));
    (services as unknown as { getTaskRequest: typeof getTaskRequest }).getTaskRequest = getTaskRequest;
    renderDialog(services, {}, "user-dialog-legacy");
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    expect(await dialog.findByText(/旧版任务；仅提供状态读取和取消请求/)).toBeInTheDocument();
    expect(await dialog.findByText("任务编号：legacy-task")).toBeInTheDocument();
    expect(await dialog.findByRole("button", { name: "请求取消" })).toBeEnabled();
    await userEvent.setup().click(dialog.getByRole("button", { name: "请求取消" }));
    expect(frameOptimize.cancel).toHaveBeenCalledWith("legacy-task", expect.any(AbortSignal));
    expect(frameOptimize.getTaskStatus).toHaveBeenCalledWith("legacy-task", expect.any(AbortSignal));
    expect(frameOptimize.submit).not.toHaveBeenCalled();
  });

  it.each([
    { name: "another task type", type: "ai-review", request: { chapter_id: chapter.id } },
    { name: "another chapter", type: "batch-optimize", request: { chapter_id: "chapter-other" } },
  ])("rejects a task with $name before exposing status or cancellation", async ({ type, request }) => {
    const { frameOptimize, services } = makeServices({
      getRunning: vi.fn(async () => ({
        task_id: "unrelated-task", status: "processing", progress: 10, progress_message: "unrelated", created_at: null,
      })),
    });
    const getTaskRequest = vi.fn(async (taskId: string) => ({
      id: taskId,
      type,
      status: "processing",
      modelName: null,
      creditCost: null,
      externalTaskId: null,
      externalProvider: null,
      createdAt: null,
      request,
    }));
    (services as unknown as { getTaskRequest: typeof getTaskRequest }).getTaskRequest = getTaskRequest;
    renderDialog(services, {}, `user-dialog-unrelated-${type}`);
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    expect(await dialog.findByRole("alert")).toHaveTextContent("无法核对其类型、章节与 frame.optimize 登记请求");
    expect(dialog.queryByText("任务编号：unrelated-task")).not.toBeInTheDocument();
    expect(dialog.queryByRole("button", { name: "请求取消" })).not.toBeInTheDocument();
    expect(frameOptimize.getTaskStatus).not.toHaveBeenCalled();
    expect(frameOptimize.cancel).not.toHaveBeenCalled();
    expect(frameOptimize.submit).not.toHaveBeenCalled();
  });

  it("treats cancel acknowledgement as a request and keeps polling authoritative status", async () => {
    const user = userEvent.setup();
    const { frameOptimize, services } = makeServices({
      getTaskStatus: vi.fn(async () => ({ ...partialStatus, status: "processing", progress: 20 })),
    });
    renderDialog(services, {}, "user-dialog-cancel");
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    await user.click(dialog.getByRole("button", { name: "全选可定位分镜" }));
    await user.click(dialog.getByRole("button", { name: "提交优化" }));
    expect(await dialog.findByText("任务状态：processing")).toBeInTheDocument();
    await user.click(dialog.getByRole("button", { name: "请求取消" }));
    expect(await dialog.findByText("服务器已收到取消请求，继续读取权威状态；不代表任务已停止或已退款。")).toBeInTheDocument();
    expect(frameOptimize.cancel).toHaveBeenCalledTimes(1);
    expect(frameOptimize.getTaskStatus).toHaveBeenCalledTimes(1);
  });

  it("does not let a late cancel response overwrite terminal source verification", async () => {
    const user = userEvent.setup();
    const pendingCancel = deferred<{ task_id: string; status: "cancelling" }>();
    const { frameOptimize, services } = makeServices({
      getTaskStatus: vi.fn()
        .mockResolvedValueOnce({ ...partialStatus, status: "processing", progress: 20 })
        .mockResolvedValueOnce({ ...partialStatus, status: "failed", progress: 100 }),
      cancel: vi.fn(() => pendingCancel.promise),
    });
    renderDialog(services, {}, "user-dialog-cancel-terminal-race");
    const dialog = within(screen.getByRole("dialog", { name: "批量优化分镜" }));
    await user.click(dialog.getByRole("button", { name: "全选可定位分镜" }));
    const submitHandler = captureButtonHandler(await dialog.findByRole("button", { name: "提交优化" }));
    vi.useFakeTimers();
    act(() => submitHandler());
    await act(async () => {
      for (let index = 0; index < 8; index += 1) await Promise.resolve();
    });
    expect(dialog.getByText("任务状态：processing")).toBeInTheDocument();

    const cancelHandler = captureButtonHandler(dialog.getByRole("button", { name: "请求取消" }));
    act(() => cancelHandler());
    expect(frameOptimize.cancel).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1200);
      for (let index = 0; index < 8; index += 1) await Promise.resolve();
    });
    expect(dialog.getByText("终态：失败（可能含部分结果）")).toBeInTheDocument();
    expect(dialog.getByText("章节源数据已核对。")).toBeInTheDocument();

    await act(async () => {
      pendingCancel.resolve({ task_id: "optimize-task-1", status: "cancelling" });
      await pendingCancel.promise;
    });
    expect(dialog.getByText("章节源数据已核对。")).toBeInTheDocument();
    expect(dialog.queryByText("服务器已收到取消请求，继续读取权威状态；不代表任务已停止或已退款。")).not.toBeInTheDocument();
  });
});
