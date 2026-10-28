import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter, Series, StoryboardAsset } from "../../shared/api/contracts";
import {
  MediaGenerationError,
  type MediaGenerationPort,
  type MediaGenerationRun,
  type VideoGenerationReceipt,
} from "../../shared/api/mediaGeneration";
import type { CreatorModel } from "../../shared/api/creatorSettings";
import { createDemoServices, type WorkspaceServices } from "../../shared/api/services";
import { unsupportedTaskHistoryTruth } from "../../shared/api/taskDetails";
import { defaultQuickReferenceSettings } from "./quickReference";
import { BatchVideoGenerationPanel } from "./BatchVideoGenerationPanel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

function seriesFixture(): Series {
  return {
    id: "series-bv", user_id: "actor-bv", name: "雾港", description: null, image_url: null,
    style_prompt_id: null, style_prompt: null, style_prompt_name: null, style_prompt_owner_name: null,
    team_id: null, team_name: null, owner_name: null, claimed_by: null, claimed_by_username: null,
    claimed_by_avatar_url: null, can_enter: true,
    created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
  };
}

function storyboardFixtures(): StoryboardAsset[] {
  return [
    { id: "storyboard-bv-1", series_id: "series-bv", chapter_id: "chapter-bv", frame_index: 0,
      name: "雾中停步", description: null, image_url: "https://media.example.test/frame-1.png",
      created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" },
    { id: "storyboard-bv-2", series_id: "series-bv", chapter_id: "chapter-bv", frame_index: 1,
      name: "回头望去", description: null, image_url: "https://media.example.test/frame-2.png",
      created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" },
  ];
}

function chapterFixture(): Chapter {
  return {
    id: "chapter-bv", series_id: "series-bv", title: "第一章",
    content: [
      { storyboard: ["storyboard-bv-1"], text: "林舟在雾中停下。" },
      { storyboard: ["storyboard-bv-2"], text: "林舟听见身后脚步声。" },
    ],
    order: 1, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z", lock: null,
  };
}

function videoModel(): CreatorModel {
  return {
    id: "video-model-bv", category: "video", name: "视频模型", provider: "fixture", model: "fixture",
    base_url: null,
    params: JSON.stringify({
      image_size: { enabled: true, default: "16:9", options: ["16:9"] },
      duration: { enabled: true, default: 5, options: [5, 10] },
      resolution: { enabled: true, default: "720p", options: ["720p"] },
      max_prompt_chars: 4096,
      billing_mode: "fixed",
      fixed_by_quality: { "720p": 2 },
    }),
    cost_per_call: 2, sort_order: 0, is_system: true, user_id: "system",
  };
}

function receipt(index: number): VideoGenerationReceipt {
  return {
    operation: "video.single", submissionId: "submission-" + index, taskId: "task-" + index,
    messageId: "message-" + index, status: "queued", quotedAmount: 2, billingStatus: "charged",
  };
}

function taskReceipt(taskId: string): Awaited<ReturnType<NonNullable<WorkspaceServices["getTaskReceipt"]>>> {
  const suffix = taskId.replace("task-", "");
  return {
    id: taskId, type: "video-single", messageId: "message-" + suffix,
    status: "processing", result: null, billingStatus: "charged", quotedAmount: 2,
    progress: 45, progressMessage: null, externalTaskId: null, externalProvider: null,
    createdAt: null, updatedAt: null,
    historyTruth: unsupportedTaskHistoryTruth(),
  };
}

function makeServices(
  submitVideo: MediaGenerationRun["submitVideo"],
  lookup: MediaGenerationRun["lookup"] = vi.fn(async () => receipt(1)),
  getTaskReceipt: WorkspaceServices["getTaskReceipt"] = vi.fn(async (taskId) => taskReceipt(taskId)),
): WorkspaceServices {
  const run: MediaGenerationRun = {
    isCurrent: () => true,
    submitVideo,
    submitImage: vi.fn(async () => ({
      operation: "image.single" as const, submissionId: "image", taskId: "image-task", messageId: "image-message",
      status: "queued" as const, quotedAmount: 1, billingStatus: "charged" as const,
    })),
    submitBatch: vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })),
    lookup,
  };
  const port: MediaGenerationPort = { beginRun: vi.fn(() => run) };
  return {
    ...createDemoServices(window.localStorage),
    mediaGeneration: port,
    getTaskReceipt,
    listFusedAssets: vi.fn(async () => []),
  };
}

function panelProps(services: WorkspaceServices, onActivityChange = vi.fn()) {
  const series = seriesFixture();
  const chapter = chapterFixture();
  const quickReferenceSettings = defaultQuickReferenceSettings();
  return {
    services,
    userId: "actor-bv",
    sessionGeneration: 1,
    series: [series],
    selectedSeriesId: series.id,
    onSeriesChange: vi.fn(),
    chapters: [chapter],
    selectedChapterId: chapter.id,
    onChapterChange: vi.fn(),
    selectedSeries: series,
    selectedChapter: chapter,
    storyboards: storyboardFixtures(),
    characters: [],
    scenes: [],
    props: [],
    models: [videoModel()],
    prompts: [],
    quickReferenceSettings: {
      ...quickReferenceSettings,
      video: { ...quickReferenceSettings.video, matchDuration: false, characterVoice: false },
    },
    loading: false,
    chapterLoading: false,
    directoryLoading: false,
    chapterError: null,
    directoryError: null,
    onUnauthorized: vi.fn(),
    onOpenTasks: vi.fn(),
    onActivityChange,
  };
}

async function selectTwoFramesAndDurations(user: ReturnType<typeof userEvent.setup>) {
  const frameGroup = screen.getByRole("group", { name: /选择分镜/u });
  const checkboxes = within(frameGroup).getAllByRole("checkbox");
  await user.click(checkboxes[0]!);
  await user.click(checkboxes[1]!);
  await user.selectOptions(screen.getByLabelText("第 1 镜 · 雾中停步时长"), "5");
  await user.selectOptions(screen.getByLabelText("第 2 镜 · 回头望去时长"), "10");
}

describe("BatchVideoGenerationPanel", () => {
  it("submits selected frames in order and refreshes their bound task receipts", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const originalSetItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation((key, value) => {
      if (key.startsWith("haoai:batch-video-preferences:v1:")) throw new Error("storage unavailable");
      originalSetItem.call(window.localStorage, key, value);
    });
    let receiptSequence = 0;
    const submitVideo = vi.fn<MediaGenerationRun["submitVideo"]>(async (_input, _signal) => receipt(++receiptSequence));
    const getTaskReceipt = vi.fn<NonNullable<WorkspaceServices["getTaskReceipt"]>>(async (taskId) => taskReceipt(taskId));
    const services = makeServices(submitVideo, undefined, getTaskReceipt);
    const props = panelProps(services);
    const { rerender } = render(<BatchVideoGenerationPanel {...props} />);

    await user.click(within(screen.getByRole("group", { name: "分镜资产检查范围" }))
      .getByRole("checkbox", { name: "角色" }));
    expect(await screen.findByText("浏览器未能保存偏好；当前表单仍可继续使用。")).toBeInTheDocument();
    rerender(<BatchVideoGenerationPanel {...props} loading />);
    rerender(<BatchVideoGenerationPanel {...props} loading={false} />);
    expect(within(screen.getByRole("group", { name: "分镜资产检查范围" }))
      .getByRole("checkbox", { name: "角色" })).not.toBeChecked();

    await selectTwoFramesAndDurations(user);
    await user.click(screen.getByRole("button", { name: "提交 2 个视频任务" }));
    await waitFor(() => expect(submitVideo).toHaveBeenCalledTimes(2), { timeout: 6000 });

    const first = submitVideo.mock.calls[0]![0];
    const second = submitVideo.mock.calls[1]![0];
    expect(first).toMatchObject({
      target: { kind: "frame_message", chapter_id: "chapter-bv", storyboard_asset_id: "storyboard-bv-1" },
      duration: 5, referenceImages: ["https://media.example.test/frame-1.png"],
    });
    expect(first.prompt).toContain("林舟在雾中停下。");
    expect(second).toMatchObject({
      target: { kind: "frame_message", chapter_id: "chapter-bv", storyboard_asset_id: "storyboard-bv-2" },
      duration: 10, referenceImages: ["https://media.example.test/frame-2.png"],
    });
    expect(second.prompt).toContain("林舟听见身后脚步声。");
    expect(first.idempotencyKey).not.toBe(second.idempotencyKey);
    expect(confirm).toHaveBeenCalledOnce();

    await screen.findAllByText("任务状态：处理中 · 45%", {}, { timeout: 5000 });
    expect(getTaskReceipt).toHaveBeenCalledTimes(2);
    await user.click(screen.getAllByRole("button", { name: "刷新任务状态" })[0]!);
    await waitFor(() => expect(getTaskReceipt).toHaveBeenCalledTimes(3));
  }, 12000);

  it("stops siblings on unknown, only reads during lookup, then retries the same request key", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let finishSubmission: ((value: VideoGenerationReceipt) => void) | undefined;
    const submitVideo = vi.fn<MediaGenerationRun["submitVideo"]>()
      .mockRejectedValueOnce(new MediaGenerationError("unknown-outcome", "connection lost"))
      .mockResolvedValueOnce(receipt(1))
      .mockImplementationOnce(() => new Promise((resolve) => { finishSubmission = resolve; }));
    const lookup = vi.fn<MediaGenerationRun["lookup"]>(async () => {
      throw new MediaGenerationError("read-error", "lookup unavailable");
    });
    const services = makeServices(submitVideo, lookup);
    render(<BatchVideoGenerationPanel {...panelProps(services)} />);

    await selectTwoFramesAndDurations(user);
    await user.click(screen.getByRole("button", { name: "提交 2 个视频任务" }));
    await screen.findByText("受理状态未知；没有自动重发。", { exact: false });
    expect(submitVideo).toHaveBeenCalledOnce();
    expect(screen.getByText("前一项未能确认，已停止后续提交。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "明确继续未发送分镜" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "核对原任务" }));
    await screen.findByText("暂时无法核对原任务；没有重发，可以稍后手动核对。");
    expect(lookup).toHaveBeenCalledOnce();
    expect(submitVideo).toHaveBeenCalledOnce();

    await user.click(screen.getByRole("button", { name: "按同一操作重试" }));
    await waitFor(() => expect(submitVideo).toHaveBeenCalledTimes(2));
    expect(submitVideo.mock.calls[1]![0]).toEqual(submitVideo.mock.calls[0]![0]);
    expect(submitVideo.mock.calls[1]![0].idempotencyKey).toBe(submitVideo.mock.calls[0]![0].idempotencyKey);
    await user.click(await screen.findByRole("button", { name: "明确继续未发送分镜" }));
    await waitFor(() => expect(submitVideo).toHaveBeenCalledTimes(3), { timeout: 5000 });
    expect(submitVideo.mock.calls[2]![0].target).toMatchObject({ storyboard_asset_id: "storyboard-bv-2" });
    expect(screen.getByLabelText("第 1 镜 · 雾中停步时长")).toBeDisabled();
    finishSubmission?.(receipt(2));
    await waitFor(() => expect(screen.getByLabelText("第 1 镜 · 雾中停步时长")).toBeEnabled());
  }, 12000);

  it("releases local activity on scope change and ignores the late old receipt", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let finishSubmission: ((receipt: VideoGenerationReceipt) => void) | undefined;
    const submitVideo = vi.fn<MediaGenerationRun["submitVideo"]>(() => new Promise((resolve) => {
      finishSubmission = resolve;
    }));
    const services = makeServices(submitVideo);
    const onActivityChange = vi.fn();
    const props = panelProps(services, onActivityChange);
    const { rerender } = render(<BatchVideoGenerationPanel {...props} />);
    const frameGroup = screen.getByRole("group", { name: /选择分镜/u });
    await user.click(within(frameGroup).getAllByRole("checkbox")[0]!);
    await user.selectOptions(screen.getByLabelText("第 1 镜 · 雾中停步时长"), "5");
    await user.click(screen.getByRole("button", { name: "提交 1 个视频任务" }));
    await waitFor(() => expect(submitVideo).toHaveBeenCalledOnce());
    expect(onActivityChange).toHaveBeenLastCalledWith(true);

    rerender(<BatchVideoGenerationPanel {...props} userId="actor-bv-next" sessionGeneration={2} />);
    await waitFor(() => expect(onActivityChange).toHaveBeenLastCalledWith(false));
    finishSubmission?.(receipt(9));
    await waitFor(() => expect(screen.queryByText(/已受理/u)).not.toBeInTheDocument());
    expect(submitVideo).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "提交 1 个视频任务" })).toBeEnabled();
  }, 8000);

  it("waits for an explicit compact choice before changing an overlong frozen prompt", async () => {
    const user = userEvent.setup();
    let finishSubmission: ((value: VideoGenerationReceipt) => void) | undefined;
    const submitVideo = vi.fn<MediaGenerationRun["submitVideo"]>(() => new Promise((resolve) => { finishSubmission = resolve; }));
    const services = makeServices(submitVideo);
    const base = panelProps(services);
    const limitedModel = videoModel();
    limitedModel.params = JSON.stringify({
      image_size: { enabled: true, default: "16:9", options: ["16:9"] },
      duration: { enabled: true, default: 5, options: [5] },
      resolution: { enabled: true, default: "720p", options: ["720p"] },
      max_prompt_chars: 1,
      billing_mode: "fixed",
    });
    const series = { ...base.selectedSeries!, style_prompt_id: "style-bv" };
    const chapter = { ...base.selectedChapter!, content: [{ storyboard: ["storyboard-bv-1"], text: "短镜头" }] };
    const props = {
      ...base,
      series: [series],
      selectedSeries: series,
      selectedChapter: chapter,
      models: [limitedModel],
      prompts: [{ id: "style-bv", category: "style", name: "长风格", user_prompt: "【风格提示词】" + "电影风格".repeat(12),
        sort_order: 0, is_system: true, user_id: "system" }],
    };
    render(<BatchVideoGenerationPanel {...props} />);
    const frameGroup = screen.getByRole("group", { name: /选择分镜/u });
    await user.click(within(frameGroup).getAllByRole("checkbox")[0]!);
    await user.selectOptions(screen.getByLabelText("第 1 镜 · 雾中停步时长"), "5");
    await user.click(screen.getByRole("button", { name: "提交 1 个视频任务" }));

    expect(await screen.findByRole("dialog", { name: "确认超长提示词处理" })).toBeInTheDocument();
    expect(submitVideo).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "完整提示词提交" })).toBeInTheDocument();
    expect(screen.getByText(/精简后仍超过模型参考上限/u)).toBeInTheDocument();
    const compactButton = screen.getByRole("button", { name: "精简后仍超限，仍然尝试" });
    const fullButton = screen.getByRole("button", { name: "完整提示词提交" });
    const cancelButton = screen.getByRole("button", { name: "取消并返回修改" });
    act(() => {
      fireEvent.click(compactButton);
      fireEvent.click(fullButton);
      fireEvent.click(cancelButton);
    });
    await waitFor(() => expect(submitVideo).toHaveBeenCalledOnce());
    expect(screen.getByLabelText("第 1 镜 · 雾中停步时长")).toBeDisabled();
    expect(submitVideo.mock.calls[0]![0].prompt).toContain("短镜头");
    expect(submitVideo.mock.calls[0]![0].prompt).not.toContain("电影风格");
    expect(submitVideo.mock.calls[0]![0].prompt.length).toBeGreaterThan(1);
    finishSubmission?.(receipt(1));
    await waitFor(() => expect(screen.getByLabelText("第 1 镜 · 雾中停步时长")).toBeEnabled());
  }, 8000);
});
