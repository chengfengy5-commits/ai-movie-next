import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CreatorModel, CreatorPrompt, CreatorSettingsServicePort } from "../../shared/api/creatorSettings";
import { ApiError } from "../../shared/api/errors";
import type { FrameReviewCapabilities, FrameReviewResult, FrameReviewServicePort, FrameReviewTask } from "../../shared/api/frameReview";
import type { WorkspaceServices } from "../../shared/api/services";
import { FrameReviewDialog, type FrameReviewSourceCheck } from "./FrameReviewDialog";

const model: CreatorModel = {
  id: "model-1",
  category: "text",
  name: "复核模型",
  provider: "openai",
  params: null,
  sort_order: 1,
  is_system: false,
  user_id: "user-1",
};

const prompt: CreatorPrompt = {
  id: "prompt-1",
  category: "ai-review",
  name: "分镜复核提示词",
  is_system: false,
  sort_order: 1,
  user_id: "user-1",
};

const enabledCapabilities: FrameReviewCapabilities = {
  operation: "frame.review",
  enabled: true,
  reason: null,
  supported_providers: ["openai", "deepseek", "custom"],
};

const completedTask: FrameReviewTask = {
  id: "review-task-1",
  type: "ai-review",
  status: "completed",
  progress: 100,
  progress_message: null,
  result: JSON.stringify({
    chapter_id: "chapter-1",
    frame_index: 0,
    prompt_id: "prompt-1",
    optimized_text: "修订后的镜头文字",
    original_text: "原始镜头文字",
  }),
  credit_cost: 7,
  model_name: "复核模型",
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

function makeDialogServices(overrides: Partial<FrameReviewServicePort> = {}) {
  const creatorSettings: Pick<CreatorSettingsServicePort, "listModels" | "listPrompts"> = {
    listModels: vi.fn(async () => [model]),
    listPrompts: vi.fn(async () => [prompt]),
  };
  const frameReview: FrameReviewServicePort = {
    getCapabilities: vi.fn(async () => ({
      ...enabledCapabilities,
    })),
    submit: vi.fn(async () => ({ task_id: "review-task-1", model_name: "复核模型", is_system: false, credit_cost: 7 })),
    getTask: vi.fn(async () => completedTask),
    cancel: vi.fn(async () => ({ task_id: "review-task-1", status: "cancelling" as const })),
    ...overrides,
  };
  return {
    frameReview,
    services: { creatorSettings, frameReview } as unknown as WorkspaceServices,
  };
}

function renderDialog(
  services: WorkspaceServices,
  options: {
    isCurrent?: () => boolean;
    onSourceApplied?: (result: FrameReviewResult) => Promise<FrameReviewSourceCheck>;
    onUnauthorized?: () => void;
  } = {},
) {
  const onClose = vi.fn();
  const onUnauthorized = options.onUnauthorized ?? vi.fn();
  const onSourceApplied = options.onSourceApplied ?? vi.fn(async () => ({ status: "applied" as const }));
  const view = render(
    <FrameReviewDialog
      chapterId="chapter-1"
      chapterTitle="目标章节"
      frameIndex={0}
      isCurrent={options.isCurrent ?? (() => true)}
      onClose={onClose}
      onSourceApplied={onSourceApplied}
      onUnauthorized={onUnauthorized}
      services={services}
      sourceText="原始镜头文字"
      sourceVersion="chapter-version-1"
      storyboardAssetId="storyboard-1"
      seriesId="series-1"
      userId="user-1"
    />,
  );
  return { ...view, onClose, onUnauthorized, onSourceApplied };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("FrameReviewDialog", () => {
  it("submits once, validates the completed target, and waits for source verification", async () => {
    const user = userEvent.setup();
    const { frameReview, services } = makeDialogServices();
    const onSourceApplied = vi.fn(async () => ({ status: "applied" as const }));
    renderDialog(services, { onSourceApplied });

    const dialog = within(await screen.findByRole("dialog", { name: "AI 分镜复核" }));
    await user.type(await dialog.findByRole("textbox", { name: "希望核对的问题" }), "检查人物动作是否连贯");
    await user.click(dialog.getByRole("button", { name: "提交复核" }));

    expect(await screen.findByRole("heading", { name: "服务器回写后的镜头文字" })).toBeInTheDocument();
    expect(screen.getByText("修订后的镜头文字")).toBeInTheDocument();
    expect(screen.getByText("服务器已回写镜头；章节源数据已重新读取并核对。")).toBeInTheDocument();
    expect(frameReview.submit).toHaveBeenCalledTimes(1);
    expect(frameReview.submit).toHaveBeenCalledWith({
      chapter_id: "chapter-1",
      frame_index: 0,
      prompt_id: "prompt-1",
      model_id: "model-1",
      message_id: "",
      error_reason: "检查人物动作是否连贯",
      asset_context: "",
      prev_context: "",
      enable_thinking: false,
    }, expect.any(AbortSignal));
    expect(frameReview.getTask).toHaveBeenCalledTimes(1);
    expect(onSourceApplied).toHaveBeenCalledWith({
      chapter_id: "chapter-1",
      frame_index: 0,
      prompt_id: "prompt-1",
      optimized_text: "修订后的镜头文字",
      original_text: "原始镜头文字",
    });
  });

  it("keeps an uncertain submit from retrying or pretending it was queued", async () => {
    const user = userEvent.setup();
    const { frameReview, services } = makeDialogServices({
      submit: vi.fn(async () => { throw new ApiError("network", "connection lost"); }),
    });
    renderDialog(services);

    const dialog = within(await screen.findByRole("dialog", { name: "AI 分镜复核" }));
    await user.type(await dialog.findByRole("textbox", { name: "希望核对的问题" }), "核对构图");
    await user.click(dialog.getByRole("button", { name: "提交复核" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("提交结果暂时无法确认，未自动重发");
    expect(frameReview.submit).toHaveBeenCalledTimes(1);
    expect(frameReview.getTask).not.toHaveBeenCalled();
    expect(screen.queryByText(/任务编号：/)).not.toBeInTheDocument();
  });

  it("treats a cancel acknowledgement as a request while polling the real task state", async () => {
    const user = userEvent.setup();
    const { frameReview, services } = makeDialogServices({
      getTask: vi.fn(async () => ({ ...completedTask, status: "queued", progress: 0 })),
    });
    renderDialog(services);

    const dialog = within(await screen.findByRole("dialog", { name: "AI 分镜复核" }));
    await user.type(await dialog.findByRole("textbox", { name: "希望核对的问题" }), "核对动作");
    await user.click(dialog.getByRole("button", { name: "提交复核" }));
    expect(await screen.findByText("任务状态：queued")).toBeInTheDocument();
    await user.click(dialog.getByRole("button", { name: "请求取消" }));

    expect(await screen.findByText("服务器已收到取消请求，继续观察实际任务状态；这不代表任务已停止或已退款。")).toBeInTheDocument();
    expect(screen.getByText("任务状态：queued")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "服务器回写后的镜头文字" })).not.toBeInTheDocument();
    expect(frameReview.cancel).toHaveBeenCalledWith("review-task-1", expect.any(AbortSignal));
    expect(frameReview.getTask).toHaveBeenCalledTimes(1);
  });

  it("shows an uncertain cancel response and keeps observing without retrying it", async () => {
    const user = userEvent.setup();
    const { frameReview, services } = makeDialogServices({
      getTask: vi.fn(async () => ({ ...completedTask, status: "queued", progress: 0 })),
      cancel: vi.fn(async () => { throw new ApiError("network", "connection lost"); }),
    });
    renderDialog(services);

    const dialog = within(await screen.findByRole("dialog", { name: "AI 分镜复核" }));
    await user.type(await dialog.findByRole("textbox", { name: "希望核对的问题" }), "核对动作");
    await user.click(dialog.getByRole("button", { name: "提交复核" }));
    expect(await screen.findByText("任务状态：queued")).toBeInTheDocument();
    await user.click(dialog.getByRole("button", { name: "请求取消" }));

    expect(await screen.findByText("取消请求结果暂时无法确认，未自动重发；继续观察任务状态。")).toBeInTheDocument();
    expect(screen.getByText("任务状态：queued")).toBeInTheDocument();
    expect(frameReview.cancel).toHaveBeenCalledTimes(1);
    expect(frameReview.getTask).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("heading", { name: "服务器回写后的镜头文字" })).not.toBeInTheDocument();
  });

  it("keeps completed task truth ahead of a late cancel acknowledgement", async () => {
    const user = userEvent.setup();
    const taskResponse = deferred<FrameReviewTask>();
    const cancelResponse = deferred<{ task_id: string; status: "cancelling" }>();
    const { frameReview, services } = makeDialogServices({
      getTask: vi.fn(() => taskResponse.promise),
      cancel: vi.fn(() => cancelResponse.promise),
    });
    const { onUnauthorized } = renderDialog(services);

    const dialog = within(await screen.findByRole("dialog", { name: "AI 分镜复核" }));
    await user.type(await dialog.findByRole("textbox", { name: "希望核对的问题" }), "核对动作");
    await user.click(dialog.getByRole("button", { name: "提交复核" }));
    await waitFor(() => expect(frameReview.getTask).toHaveBeenCalledTimes(1));
    await user.click(dialog.getByRole("button", { name: "请求取消" }));
    expect(frameReview.cancel).toHaveBeenCalledWith("review-task-1", expect.any(AbortSignal));

    await act(async () => {
      taskResponse.resolve(completedTask);
      await taskResponse.promise;
    });
    expect(await screen.findByText("服务器已回写镜头；章节源数据已重新读取并核对。")).toBeInTheDocument();

    await act(async () => {
      cancelResponse.resolve({ task_id: "review-task-1", status: "cancelling" });
      await cancelResponse.promise;
    });

    expect(screen.getByText("服务器已回写镜头；章节源数据已重新读取并核对。")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "服务器回写后的镜头文字" })).toBeInTheDocument();
    expect(screen.getByText("修订后的镜头文字")).toBeInTheDocument();
    expect(screen.getByText(/任务编号：review-task-1/)).toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("keeps a failed task explanation ahead of a late cancel failure", async () => {
    const user = userEvent.setup();
    const taskResponse = deferred<FrameReviewTask>();
    const cancelResponse = deferred<{ task_id: string; status: "cancelling" }>();
    const failedTask = {
      ...completedTask,
      status: "failed",
      progress_message: "服务器报告此任务失败。",
    } as FrameReviewTask;
    const { frameReview, services } = makeDialogServices({
      getTask: vi.fn(() => taskResponse.promise),
      cancel: vi.fn(() => cancelResponse.promise),
    });
    renderDialog(services);

    const dialog = within(await screen.findByRole("dialog", { name: "AI 分镜复核" }));
    await user.type(await dialog.findByRole("textbox", { name: "希望核对的问题" }), "核对动作");
    await user.click(dialog.getByRole("button", { name: "提交复核" }));
    await waitFor(() => expect(frameReview.getTask).toHaveBeenCalledTimes(1));
    await user.click(dialog.getByRole("button", { name: "请求取消" }));

    await act(async () => {
      taskResponse.resolve(failedTask);
      await taskResponse.promise;
    });
    expect(await screen.findByText("服务器报告此任务失败。")).toBeInTheDocument();
    await act(async () => {
      cancelResponse.reject(new ApiError("http", "取消请求未被接受", 409));
      await cancelResponse.promise.catch(() => undefined);
    });

    expect(screen.getByText("服务器报告此任务失败。")).toBeInTheDocument();
    expect(screen.getByText(/任务编号：review-task-1/)).toBeInTheDocument();
    expect(screen.queryByText("取消请求未被接受")).not.toBeInTheDocument();
  });

  it("ignores a late cancel 401 after task state becomes unconfirmed", async () => {
    const user = userEvent.setup();
    const taskResponse = deferred<FrameReviewTask>();
    const cancelResponse = deferred<{ task_id: string; status: "cancelling" }>();
    const { frameReview, services } = makeDialogServices({
      getTask: vi.fn(() => taskResponse.promise),
      cancel: vi.fn(() => cancelResponse.promise),
    });
    const { onUnauthorized } = renderDialog(services);

    const dialog = within(await screen.findByRole("dialog", { name: "AI 分镜复核" }));
    await user.type(await dialog.findByRole("textbox", { name: "希望核对的问题" }), "核对动作");
    await user.click(dialog.getByRole("button", { name: "提交复核" }));
    await waitFor(() => expect(frameReview.getTask).toHaveBeenCalledTimes(1));
    await user.click(dialog.getByRole("button", { name: "请求取消" }));

    await act(async () => {
      taskResponse.resolve({ ...completedTask, status: "waiting_on_external_system" });
      await taskResponse.promise;
    });
    const finalMessage = "服务器返回了无法识别的任务状态。任务没有被当作完成，请按任务编号核对。";
    expect(await screen.findByRole("alert")).toHaveTextContent(finalMessage);
    await act(async () => {
      cancelResponse.reject(new ApiError("http", "登录已失效", 401));
      await cancelResponse.promise.catch(() => undefined);
    });

    expect(screen.getByRole("alert")).toHaveTextContent(finalMessage);
    expect(screen.getByText(/任务编号：review-task-1/)).toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("does not mark an unrecognized server task state as complete", async () => {
    const user = userEvent.setup();
    const { frameReview, services } = makeDialogServices({
      getTask: vi.fn(async () => ({ ...completedTask, status: "waiting_on_external_system" })),
    });
    const onSourceApplied = vi.fn(async () => ({ status: "applied" as const }));
    renderDialog(services, { onSourceApplied });

    const dialog = within(await screen.findByRole("dialog", { name: "AI 分镜复核" }));
    await user.type(await dialog.findByRole("textbox", { name: "希望核对的问题" }), "核对动作");
    await user.click(dialog.getByRole("button", { name: "提交复核" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("无法识别的任务状态");
    expect(screen.getByText(/任务编号：review-task-1/)).toBeInTheDocument();
    expect(onSourceApplied).not.toHaveBeenCalled();
    expect(frameReview.getTask).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("heading", { name: "服务器回写后的镜头文字" })).not.toBeInTheDocument();
  });

  it("ignores a completed response that arrives after its owning scope changed", async () => {
    const user = userEvent.setup();
    const lateTask = deferred<FrameReviewTask>();
    let current = true;
    const { frameReview, services } = makeDialogServices({
      getTask: vi.fn(() => lateTask.promise),
    });
    const onSourceApplied = vi.fn(async () => ({ status: "applied" as const }));
    renderDialog(services, { isCurrent: () => current, onSourceApplied });

    const dialog = within(await screen.findByRole("dialog", { name: "AI 分镜复核" }));
    await user.type(await dialog.findByRole("textbox", { name: "希望核对的问题" }), "核对角色位置");
    await user.click(dialog.getByRole("button", { name: "提交复核" }));
    await waitFor(() => expect(frameReview.getTask).toHaveBeenCalledTimes(1));

    current = false;
    await act(async () => {
      lateTask.resolve(completedTask);
      await lateTask.promise;
    });

    expect(onSourceApplied).not.toHaveBeenCalled();
    expect(screen.queryByRole("heading", { name: "服务器回写后的镜头文字" })).not.toBeInTheDocument();
  });
});
