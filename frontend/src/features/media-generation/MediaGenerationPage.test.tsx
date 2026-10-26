import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter, Character, Series, StoryboardAsset } from "../../shared/api/contracts";
import type { MediaGenerationPort, MediaGenerationRun } from "../../shared/api/mediaGeneration";
import type { CreatorModel, CreatorPrompt, CreatorSettingsServicePort } from "../../shared/api/creatorSettings";
import { createDemoServices, type WorkspaceServices } from "../../shared/api/services";
import { MediaGenerationPage } from "./MediaGenerationPage";

afterEach(cleanup);

function creatorSettings(): CreatorSettingsServicePort {
  const imageModel: CreatorModel = {
    id: "image-model-926",
    category: "image",
    name: "图片模型",
    provider: "fixture",
    model: "image-fixture",
    base_url: null,
    params: JSON.stringify({ image_size: { enabled: true, default: "1:1", options: ["1:1", "16:9"] } }),
    sort_order: 0,
    is_system: false,
    user_id: "actor-926",
  };
  const videoModel: CreatorModel = {
    id: "video-model-926",
    category: "video",
    name: "视频模型",
    provider: "fixture",
    model: "video-fixture",
    base_url: null,
    params: JSON.stringify({
      image_size: { enabled: true, default: "16:9", options: ["16:9"] },
      duration: { enabled: true, default: 5, options: [5, 10] },
      resolution: { enabled: true, default: "720p", options: ["720p"] },
    }),
    sort_order: 0,
    is_system: false,
    user_id: "actor-926",
  };
  const prompt: CreatorPrompt = {
    id: "image-prompt-926",
    category: "character-design",
    name: "素材提示词",
    system_prompt: "保持设定一致",
    user_prompt: "细节清晰",
    sort_order: 0,
    is_system: false,
    user_id: "actor-926",
  };
  const unexpected = async (): Promise<never> => { throw new Error("Unexpected creator-setting mutation."); };
  return {
    listModels: vi.fn(async (category) => category === "video" ? [videoModel] : category === "image" ? [imageModel] : []),
    listPrompts: vi.fn(async () => [prompt]),
    getModel: unexpected,
    createModel: unexpected,
    updateModel: unexpected,
    deleteModel: unexpected,
    reorderModels: unexpected,
    getPrompt: unexpected,
    createPrompt: unexpected,
    updatePrompt: unexpected,
    deletePrompt: unexpected,
    reorderPrompts: unexpected,
  };
}

function generationPort(
  submitBatch: MediaGenerationRun["submitBatch"],
  submitVideo: MediaGenerationRun["submitVideo"] = vi.fn(async () => ({
    operation: "video.single" as const,
    submissionId: "video-submission-926",
    taskId: "video-task-926",
    messageId: "video-message-926",
    status: "queued" as const,
    quotedAmount: 3,
    billingStatus: "charged" as const,
  })),
): MediaGenerationPort {
  const run: MediaGenerationRun = {
    isCurrent: () => true,
    submitVideo,
    submitBatch,
    lookup: vi.fn(async () => ({
      operation: "image.batch" as const,
      tasks: [
        { type: "scene" as const, assetId: "scene-926", assetName: "雨巷", messageId: "message-scene", taskId: "task-scene" },
        { type: "character" as const, assetId: "character-926", assetName: "林舟", messageId: "message-character", taskId: "task-character" },
      ],
    })),
  };
  return { beginRun: vi.fn(() => run) };
}

function chapterFixture(): Chapter {
  return {
    id: "chapter-926",
    series_id: "series-926",
    title: "雨夜",
    content: [],
    order: 1,
    created_at: "2026-10-20T00:00:00Z",
    updated_at: "2026-10-20T00:00:00Z",
    lock: null,
  };
}

function seriesFixture(): Series {
  return {
    id: "series-926",
    user_id: "actor-926",
    name: "雾港来信",
    description: null,
    image_url: null,
    style_prompt_id: null,
    style_prompt: null,
    style_prompt_name: null,
    style_prompt_owner_name: null,
    team_id: null,
    team_name: null,
    owner_name: null,
    claimed_by: null,
    claimed_by_username: null,
    claimed_by_avatar_url: null,
    can_enter: true,
    created_at: "2026-10-20T00:00:00Z",
    updated_at: "2026-10-20T00:00:00Z",
  };
}

function characterFixture(): Character {
  return {
    id: "character-926",
    series_id: "series-926",
    name: "林舟",
    description: "长风衣，沉着",
    image_url: null,
    aliases: null,
    canonical_key: null,
    created_at: "2026-10-20T00:00:00Z",
    updated_at: "2026-10-20T00:00:00Z",
    gender: null,
    age: null,
    role: null,
    appearance: null,
    audio_url: null,
    voice_ref: null,
  };
}

function storyboardFixture(): StoryboardAsset {
  return {
    id: "storyboard-926",
    series_id: "series-926",
    chapter_id: "chapter-926",
    frame_index: 1,
    name: "雨巷转场镜头",
    description: "石板路上的中景转场",
    image_url: null,
    created_at: "2026-10-20T00:00:00Z",
    updated_at: "2026-10-20T00:00:00Z",
  };
}

describe("MediaGenerationPage", () => {
  it("offers a current-chapter storyboard as a video asset-message target", async () => {
    const user = userEvent.setup();
    const series = seriesFixture();
    const chapter = chapterFixture();
    const submitVideo = vi.fn<MediaGenerationRun["submitVideo"]>(async () => ({
      operation: "video.single",
      submissionId: "video-submission-926",
      taskId: "video-task-926",
      messageId: "video-message-926",
      status: "queued",
      quotedAmount: 3,
      billingStatus: "charged",
    }));
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })), submitVideo),
      listSeries: vi.fn(async () => [series]),
      listChapters: vi.fn(async () => [chapter]),
      listStoryboardAssets: vi.fn(async () => [storyboardFixture()]),
      listCharacters: vi.fn(async () => []),
      listScenes: vi.fn(async () => []),
      listProps: vi.fn(async () => []),
    };

    render(
      <MediaGenerationPage
        services={services}
        userId="actor-926"
        sessionGeneration={4}
        onUnauthorized={vi.fn()}
        onOpenTasks={vi.fn()}
      />,
    );

    await user.selectOptions(await screen.findByLabelText("视频模型"), "video-model-926");
    await user.type(screen.getByLabelText("视频提示词"), "雨巷分镜延展镜头");
    await user.selectOptions(screen.getByLabelText("视频目标"), "asset_message");
    await user.selectOptions(await screen.findByLabelText("剧集"), series.id);
    await user.selectOptions(await screen.findByLabelText("章节"), chapter.id);
    await user.selectOptions(await screen.findByLabelText("素材类型"), "storyboard");
    await user.selectOptions(await screen.findByLabelText("目标素材"), "storyboard-926");
    expect(screen.getByRole("option", { name: "第 2 镜 · 雨巷转场镜头" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "提交视频任务" }));

    await screen.findByText(/视频任务已受理/);
    expect(submitVideo).toHaveBeenCalledTimes(1);
    expect(submitVideo.mock.calls[0]![0].target).toEqual({
      kind: "asset_message",
      chapter_id: chapter.id,
      asset_type: "storyboard",
      asset_id: "storyboard-926",
    });
  });

  it("submits the selected batch once in user order and links accepted work to My Tasks", async () => {
    const user = userEvent.setup();
    const submitBatch = vi.fn<MediaGenerationRun["submitBatch"]>(async () => ({
      operation: "image.batch" as const,
      tasks: [
        { type: "storyboard" as const, assetId: "storyboard-926", assetName: "雨巷转场镜头", messageId: "message-frame", taskId: "task-frame" },
        { type: "scene" as const, assetId: "scene-926", assetName: "雨巷", messageId: "message-scene", taskId: "task-scene" },
        { type: "character" as const, assetId: "character-926", assetName: "林舟", messageId: "message-character", taskId: "task-character" },
      ],
    }));
    const onOpenTasks = vi.fn();
    const series = seriesFixture();
    const chapter = chapterFixture();
    const scene = { id: "scene-926", series_id: series.id, title: "雨巷", description: "青石路面", image_url: null, aliases: null, canonical_key: null, created_at: "2026-10-20T00:00:00Z", updated_at: "2026-10-20T00:00:00Z" };
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      mode: "api",
      apiBaseUrl: "http://127.0.0.1:4175/api",
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(submitBatch),
      listSeries: vi.fn(async () => [series]),
      listChapters: vi.fn(async () => [chapter]),
      listStoryboardAssets: vi.fn(async () => [storyboardFixture()]),
      listCharacters: vi.fn(async () => [characterFixture()]),
      listScenes: vi.fn(async () => [scene]),
      listProps: vi.fn(async () => []),
    };

    render(
      <MediaGenerationPage
        services={services}
        userId="actor-926"
        sessionGeneration={4}
        onUnauthorized={vi.fn()}
        onOpenTasks={onOpenTasks}
      />,
    );

    await user.click(screen.getByRole("button", { name: "批量素材生图" }));
    await user.selectOptions(screen.getByLabelText("剧集"), series.id);
    await user.selectOptions(await screen.findByLabelText("章节"), chapter.id);
    await user.selectOptions(await screen.findByLabelText("图片模型"), "image-model-926");
    const assetChoices = await screen.findByRole("group", { name: /选择生成素材/ });
    await user.click(within(assetChoices).getByRole("checkbox", { name: /第 2 镜 · 雨巷转场镜头/ }));
    await user.click(within(assetChoices).getByRole("checkbox", { name: /^雨巷场景 · 青石路面$/ }));
    await user.click(within(assetChoices).getByRole("checkbox", { name: /林舟/ }));
    await user.click(screen.getByRole("button", { name: "提交 3 项批量任务" }));

    await screen.findByText(/批量请求已回读，共 3 项/);
    expect(submitBatch).toHaveBeenCalledTimes(1);
    const submitted = submitBatch.mock.calls[0]![0];
    expect(submitted).toMatchObject({ chapterId: chapter.id, assets: [
      { type: "storyboard", id: "storyboard-926", name: "雨巷转场镜头", description: "石板路上的中景转场", modelId: "image-model-926" },
      { type: "scene", id: "scene-926", modelId: "image-model-926" },
      { type: "character", id: "character-926", modelId: "image-model-926" },
    ] });
    expect(submitted.idempotencyKey).toEqual(expect.any(String));

    await user.click(screen.getByRole("button", { name: "查看我的任务" }));
    expect(onOpenTasks).toHaveBeenCalledTimes(1);
  });
});
