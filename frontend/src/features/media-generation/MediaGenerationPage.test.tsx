import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter, Character, Series, StoryboardAsset } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import { MediaGenerationError, type MediaGenerationPort, type MediaGenerationRun } from "../../shared/api/mediaGeneration";
import type { MediaReferenceUploadPort, MediaReferenceUploadRun } from "../../shared/api/mediaReferenceUpload";
import type { CreatorModel, CreatorPrompt, CreatorSettingsServicePort } from "../../shared/api/creatorSettings";
import type { UserPreferences, UserPreferencesPort } from "../../shared/api/userPreferences";
import { createDemoServices, type WorkspaceServices } from "../../shared/api/services";
import { MediaGenerationPage } from "./MediaGenerationPage";
import {
  readSingleMediaModePreference,
  readSingleMediaPreference,
  singleMediaModePreferenceKey,
  singleMediaPreferenceKey,
  writeSingleMediaModePreference,
  writeSingleMediaPreference,
} from "./mediaSelectionPreferences";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

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
  const sceneImageModel: CreatorModel = {
    ...imageModel,
    id: "image-model-scene-926",
    name: "场景图片模型",
    params: JSON.stringify({ image_size: { enabled: true, default: "2:3", options: ["2:3", "4:3"] } }),
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
  const videoPrompt: CreatorPrompt = {
    ...prompt,
    id: "video-prompt-926",
    category: "video-design",
    name: "视频提示词",
    system_prompt: "保持镜头运动连贯",
    user_prompt: "生成自然的镜头延展",
  };
  const prompts: CreatorPrompt[] = [
    prompt,
    videoPrompt,
    { ...prompt, id: "image-prompt-scene-926", category: "scene-design", name: "场景提示词" },
    { ...prompt, id: "image-prompt-prop-926", category: "prop-design", name: "道具提示词" },
    { ...prompt, id: "image-prompt-storyboard-926", category: "storyboard-design", name: "分镜提示词" },
  ];
  const unexpected = async (): Promise<never> => { throw new Error("Unexpected creator-setting mutation."); };
  return {
    listModels: vi.fn(async (category) => category === "video" ? [videoModel] : category === "image" ? [imageModel, sceneImageModel] : []),
    listPrompts: vi.fn(async () => prompts),
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
  submitImage: MediaGenerationRun["submitImage"] = vi.fn(async () => ({
    operation: "image.single" as const,
    submissionId: "image-submission-936",
    taskId: "image-task-936",
    messageId: "image-message-936",
    status: "queued" as const,
    quotedAmount: 4,
    billingStatus: "charged" as const,
  })),
  lookup: MediaGenerationRun["lookup"] = vi.fn(async () => ({
    operation: "image.batch" as const,
    tasks: [
      { type: "scene" as const, assetId: "scene-926", assetName: "雨巷", messageId: "message-scene", taskId: "task-scene" },
      { type: "character" as const, assetId: "character-926", assetName: "林舟", messageId: "message-character", taskId: "task-character" },
    ],
  })),
): MediaGenerationPort {
  const run: MediaGenerationRun = {
    isCurrent: () => true,
    submitImage,
    submitVideo,
    submitBatch,
    lookup,
  };
  return { beginRun: vi.fn(() => run) };
}

function userPreferencesPort(initial: UserPreferences) {
  let preferences: UserPreferences = JSON.parse(JSON.stringify(initial)) as UserPreferences;
  const get = vi.fn(async () => JSON.parse(JSON.stringify(preferences)) as UserPreferences);
  const update = vi.fn(async (value: UserPreferences) => {
    preferences = { ...preferences, ...JSON.parse(JSON.stringify(value)) as UserPreferences };
    return { updatedKeys: Object.keys(value) };
  });
  const port: UserPreferencesPort = {
    get,
    update,
  };
  return { port, current: () => preferences, get, update };
}

function referenceUploadPort(
  upload: MediaReferenceUploadRun["upload"] = vi.fn(async () => "https://cdn.example.test/reference.png"),
): MediaReferenceUploadPort {
  return {
    beginRun: vi.fn((context) => ({
      isCurrent: () => context.isContextCurrent(),
      upload,
    })),
  };
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
  it("offers a user-visible batch storyboard video workflow", async () => {
    const user = userEvent.setup();
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
    };
    render(<MediaGenerationPage services={services} userId="actor-batch-video-entry" sessionGeneration={1}
      onUnauthorized={vi.fn()} onOpenTasks={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "批量分镜视频" }));
    expect(await screen.findByRole("heading", { name: "批量分镜视频" })).toBeInTheDocument();
  });

  it("isolates local single-media selections by account, API, mode, and target category", () => {
    const storage = window.localStorage;
    const scope = { apiBaseUrl: "https://api.example.test/api", userId: "actor-media-pref" };
    const imagePreference = { modelId: "image-model", promptId: "image-prompt", size: "16:9", generationCount: 1 };
    const videoPreference = {
      modelId: "video-model",
      promptId: "video-prompt",
      size: "16:9",
      generationCount: 1,
      duration: "10",
      resolution: "1080p",
    };

    expect(writeSingleMediaPreference(scope, "image", "frame", imagePreference, storage)).toBe(true);
    expect(writeSingleMediaPreference(scope, "video", "frame", videoPreference, storage)).toBe(true);
    expect(readSingleMediaPreference(scope, "image", "frame", storage)).toEqual({ kind: "found", value: imagePreference });
    expect(readSingleMediaPreference(scope, "video", "frame", storage)).toEqual({ kind: "found", value: videoPreference });
    expect(readSingleMediaPreference(scope, "image", "general", storage)).toEqual({ kind: "missing" });
    expect(readSingleMediaPreference({ ...scope, userId: "another-actor" }, "image", "frame", storage))
      .toEqual({ kind: "missing" });
    expect(readSingleMediaPreference({ ...scope, apiBaseUrl: "https://other.example.test/api" }, "image", "frame", storage))
      .toEqual({ kind: "missing" });
    expect(singleMediaPreferenceKey(scope, "image", "frame"))
      .not.toBe(singleMediaPreferenceKey(scope, "video", "frame"));

    expect(writeSingleMediaModePreference(scope, "frame", "image", storage)).toBe(true);
    expect(readSingleMediaModePreference(scope, "frame", storage)).toEqual({ kind: "found", value: "image" });
    expect(readSingleMediaModePreference({ ...scope, userId: "another-actor" }, "frame", storage)).toEqual({ kind: "missing" });
    expect(singleMediaModePreferenceKey(scope, "frame")).not.toBe(singleMediaModePreferenceKey(scope, "general"));

    const unavailable = {
      getItem: () => { throw new Error("storage unavailable"); },
      setItem: () => { throw new Error("storage unavailable"); },
    } as unknown as Storage;
    expect(readSingleMediaPreference(scope, "image", "frame", unavailable)).toEqual({ kind: "unavailable" });
    expect(writeSingleMediaPreference(scope, "image", "frame", imagePreference, unavailable)).toBe(false);
  });

  it("restores a category mode into the existing image form and remembers its count", async () => {
    const user = userEvent.setup();
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
    };
    const scope = { apiBaseUrl: services.apiBaseUrl, userId: "actor-926" };
    expect(writeSingleMediaModePreference(scope, "scene", "image")).toBe(true);
    expect(writeSingleMediaPreference(scope, "image", "scene", {
      modelId: "image-model-926",
      promptId: "",
      size: "1:1",
      generationCount: 3,
    })).toBe(true);
    render(<MediaGenerationPage services={services} userId={scope.userId} sessionGeneration={4}
      onUnauthorized={vi.fn()} onOpenTasks={vi.fn()} />);

    await user.selectOptions(await screen.findByLabelText("视频目标"), "asset_message");
    await user.selectOptions(screen.getByLabelText("素材类型"), "scene");
    await waitFor(() => expect(screen.getByRole("button", { name: "普通图片" })).toHaveAttribute("aria-pressed", "true"));
    await waitFor(() => expect(screen.getByLabelText("生成数量")).toHaveValue("3"));
    expect(await screen.findByText("已恢复当前分类的图片模式。")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("图片模型"), "image-model-scene-926");
    expect(screen.getByLabelText("生成数量")).toHaveValue("1");
    expect(readSingleMediaPreference(scope, "image", "scene")).toMatchObject({
      kind: "found",
      value: { modelId: "image-model-scene-926", generationCount: 1 },
    });
  });

  it("restores a manually remembered mode when returning to its target category", async () => {
    const user = userEvent.setup();
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
    };
    const scope = { apiBaseUrl: services.apiBaseUrl, userId: "actor-mode-return-1067" };
    expect(writeSingleMediaModePreference(scope, "scene", "video")).toBe(true);
    render(<MediaGenerationPage services={services} userId={scope.userId} sessionGeneration={1}
      onUnauthorized={vi.fn()} onOpenTasks={vi.fn()} />);

    await user.selectOptions(await screen.findByLabelText("视频目标"), "asset_message");
    await user.selectOptions(screen.getByLabelText("素材类型"), "character");
    await user.click(screen.getByRole("button", { name: "普通图片" }));
    expect(readSingleMediaModePreference(scope, "character")).toEqual({ kind: "found", value: "image" });

    await user.selectOptions(screen.getByLabelText("素材类型"), "scene");
    await waitFor(() => expect(screen.getByRole("button", { name: "普通视频" })).toHaveAttribute("aria-pressed", "true"));
    await user.selectOptions(screen.getByLabelText("素材类型"), "character");
    await waitFor(() => expect(screen.getByRole("button", { name: "普通图片" })).toHaveAttribute("aria-pressed", "true"));
  });

  it("keeps a prompt typed before the model catalog finishes loading", async () => {
    const user = userEvent.setup();
    const baseSettings = creatorSettings();
    let releaseCatalog: (() => void) | undefined;
    const catalogGate = new Promise<void>((resolve) => { releaseCatalog = resolve; });
    const delayedSettings: CreatorSettingsServicePort = {
      ...baseSettings,
      listModels: async (category, signal) => {
        await catalogGate;
        return baseSettings.listModels(category, signal);
      },
      listPrompts: async (category, signal) => {
        await catalogGate;
        return baseSettings.listPrompts(category, signal);
      },
    };
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: delayedSettings,
    };
    expect(writeSingleMediaModePreference({ apiBaseUrl: services.apiBaseUrl, userId: "actor-mode-draft-1067" }, "general", "image"))
      .toBe(true);
    render(<MediaGenerationPage services={services} userId="actor-mode-draft-1067" sessionGeneration={1}
      onUnauthorized={vi.fn()} onOpenTasks={vi.fn()} />);

    await user.type(screen.getByLabelText("视频提示词"), "目录加载时输入的内容");
    expect(screen.getByRole("button", { name: "普通视频" })).toHaveAttribute("aria-pressed", "true");
    releaseCatalog?.();

    expect(await screen.findByText("当前表单已有编辑或参考内容；为保留当前输入，暂不自动切换到此分类记住的模式。可手动选择图片或视频。"))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: "普通视频" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("视频提示词")).toHaveValue("目录加载时输入的内容");
  });

  it("submits a normal image to the selected storyboard message and links it to My Tasks", async () => {
    const user = userEvent.setup();
    const series = seriesFixture();
    const chapter = chapterFixture();
    const submitImage = vi.fn<MediaGenerationRun["submitImage"]>(async () => ({
      operation: "image.single",
      submissionId: "image-submission-936",
      taskId: "image-task-936",
      messageId: "image-message-936",
      status: "queued",
      quotedAmount: 4,
      billingStatus: "charged",
    }));
    const uploadReference = vi.fn<MediaReferenceUploadRun["upload"]>(async () => "https://cdn.example.test/uploaded-reference.png");
    const onOpenTasks = vi.fn();
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })), undefined, submitImage),
      mediaReferenceUpload: referenceUploadPort(uploadReference),
      listSeries: vi.fn(async () => [series]),
      listChapters: vi.fn(async () => [chapter]),
      listStoryboardAssets: vi.fn(async () => [storyboardFixture()]),
      listCharacters: vi.fn(async () => []),
      listScenes: vi.fn(async () => []),
      listProps: vi.fn(async () => []),
    };
    expect(writeSingleMediaPreference({ apiBaseUrl: services.apiBaseUrl, userId: "actor-926" }, "image", "frame", {
      modelId: "image-model-926",
      promptId: "image-prompt-926",
      size: "1:1",
    })).toBe(true);
    const originalSetItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation((key, value) => {
      if (key.startsWith("haoai:media-generation-selection:v1:")) throw new Error("storage unavailable");
      originalSetItem.call(window.localStorage, key, value);
    });

    render(
      <MediaGenerationPage
        services={services}
        userId="actor-926"
        sessionGeneration={4}
        onUnauthorized={vi.fn()}
        onOpenTasks={onOpenTasks}
      />,
    );

    await user.click(screen.getByRole("button", { name: "普通图片" }));
    await user.selectOptions(screen.getByLabelText("图片目标"), "frame_message");
    await waitFor(() => {
      expect(screen.getByLabelText("图片模型")).toHaveValue("image-model-926");
      expect(screen.getByLabelText("提示词目录")).toHaveValue("image-prompt-926");
      expect(screen.getByLabelText("图片尺寸")).toHaveValue("1:1");
    });
    expect(screen.getByLabelText("图片提示词")).toHaveValue("保持设定一致\n\n细节清晰");
    await user.selectOptions(screen.getByLabelText("图片尺寸"), "16:9");
    expect(await screen.findByText("浏览器未能保存此分类的生成选项；当前表单仍可继续使用。")).toBeInTheDocument();
    await user.selectOptions(await screen.findByLabelText("剧集"), series.id);
    await user.selectOptions(await screen.findByLabelText("章节"), chapter.id);
    await user.selectOptions(await screen.findByLabelText("分镜目标"), "storyboard-926");
    await user.type(screen.getByLabelText("参考图片 URL（每行一项）"), "https://example.test/ref-a.png\nhttps://example.test/ref-b.png");
    await user.upload(screen.getByLabelText("选择图片文件"), new File(["image"], "reference.png", { type: "image/png" }));
    await user.click(screen.getByRole("button", { name: "上传并添加图片到参考列表" }));
    await screen.findByText("已上传并添加到当前参考图片列表。");
    expect(uploadReference).toHaveBeenCalledOnce();
    expect(submitImage).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "提交图片任务" }));

    await screen.findByText(/图片任务已受理/);
    expect(submitImage).toHaveBeenCalledTimes(1);
    expect(submitImage.mock.calls[0]![0]).toMatchObject({
      modelId: "image-model-926",
      prompt: "保持设定一致\n\n细节清晰",
      target: { kind: "frame_message", chapter_id: chapter.id, storyboard_asset_id: "storyboard-926" },
      size: "16:9",
      referenceImages: [
        "https://example.test/ref-a.png",
        "https://example.test/ref-b.png",
        "https://cdn.example.test/uploaded-reference.png",
      ],
    });
    expect(submitImage.mock.calls[0]![0].idempotencyKey).toEqual(expect.any(String));

    await user.click(screen.getByRole("button", { name: "查看我的任务" }));
    expect(onOpenTasks).toHaveBeenCalledTimes(1);
  });

  it("submits a selected image count as ordered single requests with independent keys and nullable message ids", async () => {
    const user = userEvent.setup();
    let sequence = 0;
    const submitImage = vi.fn<MediaGenerationRun["submitImage"]>(async () => {
      sequence += 1;
      return {
        operation: "image.single",
        submissionId: `count-submission-${sequence}`,
        taskId: `count-task-${sequence}`,
        messageId: null,
        status: "queued",
        quotedAmount: 1,
        billingStatus: "charged",
      };
    });
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })), undefined, submitImage),
    };
    render(<MediaGenerationPage services={services} userId="actor-926" sessionGeneration={4}
      onUnauthorized={vi.fn()} onOpenTasks={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "普通图片" }));
    await user.selectOptions(await screen.findByLabelText("图片模型"), "image-model-926");
    await user.type(screen.getByLabelText("图片提示词"), "同一冻结正文");
    await user.selectOptions(screen.getByLabelText("生成数量"), "3");
    await user.click(screen.getByRole("button", { name: "提交图片任务" }));

    expect(await screen.findByText(/3 项图片任务已按顺序受理/u)).toBeInTheDocument();
    expect(submitImage).toHaveBeenCalledTimes(3);
    const inputs = submitImage.mock.calls.map(([input]) => input);
    expect(inputs.map((input) => input.idempotencyKey)).toHaveLength(3);
    expect(new Set(inputs.map((input) => input.idempotencyKey)).size).toBe(3);
    expect(inputs.map(({ modelId, prompt, target }) => ({ modelId, prompt, target }))).toEqual([
      { modelId: "image-model-926", prompt: "同一冻结正文", target: { kind: "standalone" } },
      { modelId: "image-model-926", prompt: "同一冻结正文", target: { kind: "standalone" } },
      { modelId: "image-model-926", prompt: "同一冻结正文", target: { kind: "standalone" } },
    ]);
    expect(inputs[0]?.idempotencyKey).not.toBe(inputs[1]?.idempotencyKey);
    expect(screen.getAllByText(/提交回执状态：queued；消息编号：—/u)).toHaveLength(3);
    expect(screen.getAllByText(/消息编号：—/u)).toHaveLength(3);
  });

  it("submits a selected video count in order with frozen video parameters", async () => {
    const user = userEvent.setup();
    let sequence = 0;
    const submitVideo = vi.fn<MediaGenerationRun["submitVideo"]>(async () => {
      sequence += 1;
      return {
        operation: "video.single",
        submissionId: `video-count-submission-${sequence}`,
        taskId: `video-count-task-${sequence}`,
        messageId: null,
        status: "queued",
        quotedAmount: 1,
        billingStatus: "charged",
      };
    });
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })), submitVideo),
    };
    render(<MediaGenerationPage services={services} userId="actor-926" sessionGeneration={4}
      onUnauthorized={vi.fn()} onOpenTasks={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "普通视频" }));
    await user.selectOptions(await screen.findByLabelText("视频模型"), "video-model-926");
    await user.type(screen.getByLabelText("视频提示词"), "冻结的视频提示词");
    await user.selectOptions(screen.getByLabelText("生成数量"), "2");
    await user.click(screen.getByRole("button", { name: "提交视频任务" }));

    expect(await screen.findByText(/2 项视频任务已按顺序受理/u)).toBeInTheDocument();
    expect(submitVideo).toHaveBeenCalledTimes(2);
    const inputs = submitVideo.mock.calls.map(([input]) => input);
    expect(new Set(inputs.map((input) => input.idempotencyKey)).size).toBe(2);
    expect(inputs.map(({ modelId, prompt, target, size, duration, resolution }) => ({
      modelId, prompt, target, size, duration, resolution,
    }))).toEqual([
      {
        modelId: "video-model-926",
        prompt: "冻结的视频提示词",
        target: { kind: "standalone" },
        size: "16:9",
        duration: 5,
        resolution: "720p",
      },
      {
        modelId: "video-model-926",
        prompt: "冻结的视频提示词",
        target: { kind: "standalone" },
        size: "16:9",
        duration: 5,
        resolution: "720p",
      },
    ]);
    expect(screen.getAllByText(/消息编号：—/u)).toHaveLength(2);
  });

  it("refreshes each accepted task only when its receipt identity matches, including a null message id", async () => {
    const user = userEvent.setup();
    let sequence = 0;
    const submitImage = vi.fn<MediaGenerationRun["submitImage"]>(async () => {
      sequence += 1;
      return {
        operation: "image.single",
        submissionId: `status-submission-${sequence}`,
        taskId: `status-task-${sequence}`,
        messageId: sequence === 1 ? null : "status-message-2",
        status: "queued",
        quotedAmount: 1,
        billingStatus: "charged",
      };
    });
    const getTaskReceipt = vi.fn<NonNullable<WorkspaceServices["getTaskReceipt"]>>(async (taskId) => ({
      id: taskId,
      type: "image-single",
      messageId: taskId === "status-task-1" ? null : "wrong-message-id",
      status: taskId === "status-task-1" ? "processing" : "completed",
      result: null,
      billingStatus: "charged",
      quotedAmount: 1,
      progress: 35,
      progressMessage: null,
      externalTaskId: null,
      externalProvider: null,
      createdAt: null,
      updatedAt: null,
    }));
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })), undefined, submitImage),
      getTaskReceipt,
    };
    render(<MediaGenerationPage services={services} userId="actor-task-status-1067" sessionGeneration={1}
      onUnauthorized={vi.fn()} onOpenTasks={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "普通图片" }));
    await user.selectOptions(await screen.findByLabelText("图片模型"), "image-model-926");
    await user.type(screen.getByLabelText("图片提示词"), "按原任务身份读取状态");
    await user.selectOptions(screen.getByLabelText("生成数量"), "2");
    await user.click(screen.getByRole("button", { name: "提交图片任务" }));
    await screen.findByText(/2 项图片任务已按顺序受理/u);

    expect(getTaskReceipt).not.toHaveBeenCalled();
    const firstItem = screen.getByText("第 1 项 · 已受理").closest("li");
    const secondItem = screen.getByText("第 2 项 · 已受理").closest("li");
    expect(firstItem).not.toBeNull();
    expect(secondItem).not.toBeNull();
    await user.click(within(firstItem!).getByRole("button", { name: "刷新第 1 项任务状态" }));
    await user.click(within(secondItem!).getByRole("button", { name: "刷新第 2 项任务状态" }));

    await within(firstItem!).findByText("任务状态：processing · 35%");
    expect(within(secondItem!).getByText("任务状态与原受理回执不匹配，未显示本次状态。"))
      .toBeInTheDocument();
    expect(within(secondItem!).queryByText(/任务状态：completed/u)).not.toBeInTheDocument();
    expect(getTaskReceipt.mock.calls.map(([taskId]) => taskId)).toEqual(["status-task-1", "status-task-2"]);
  });

  it("keeps a single-item request and its unknown status visible for recovery", async () => {
    const user = userEvent.setup();
    const submitImage = vi.fn<MediaGenerationRun["submitImage"]>(async () => {
      throw new MediaGenerationError("unknown-outcome", "响应超时");
    });
    const lookup = vi.fn<MediaGenerationRun["lookup"]>(async () => {
      throw new MediaGenerationError("unknown-outcome", "仍无核对回执");
    });
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })), undefined, submitImage, lookup),
    };
    render(<MediaGenerationPage services={services} userId="actor-single-unknown-1067" sessionGeneration={1}
      onUnauthorized={vi.fn()} onOpenTasks={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "普通图片" }));
    await user.selectOptions(await screen.findByLabelText("图片模型"), "image-model-926");
    await user.type(screen.getByLabelText("图片提示词"), "待确认时仍可核对的正文");
    await user.click(screen.getByRole("button", { name: "提交图片任务" }));

    expect(await screen.findByText("第 1 项 · 受理待确认")).toBeInTheDocument();
    expect(screen.getByText("本组继续使用提交时冻结的请求；已受理项目和待确认项目会保留在这里。")).toBeInTheDocument();
    expect(submitImage).toHaveBeenCalledOnce();
    expect(lookup).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: /按原请求重试本项/u })).toBeInTheDocument();
  });

  it("keeps the new actor request busy when the prior actor submit resolves late", async () => {
    const user = userEvent.setup();
    let finishFirst: ((receipt: Awaited<ReturnType<MediaGenerationRun["submitImage"]>>) => void) | undefined;
    let finishSecond: ((receipt: Awaited<ReturnType<MediaGenerationRun["submitImage"]>>) => void) | undefined;
    const receipt = (suffix: string) => ({
      operation: "image.single" as const,
      submissionId: `scope-submission-${suffix}`,
      taskId: `scope-task-${suffix}`,
      messageId: null,
      status: "queued" as const,
      quotedAmount: 1,
      billingStatus: "charged" as const,
    });
    const submitImage = vi.fn<MediaGenerationRun["submitImage"]>()
      .mockImplementationOnce(() => new Promise((resolve) => { finishFirst = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { finishSecond = resolve; }));
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })), undefined, submitImage),
    };
    const props = { services, sessionGeneration: 1, onUnauthorized: vi.fn(), onOpenTasks: vi.fn() };
    const { rerender } = render(<MediaGenerationPage {...props} userId="actor-scope-old-1067" />);

    await user.click(screen.getByRole("button", { name: "普通图片" }));
    await user.selectOptions(await screen.findByLabelText("图片模型"), "image-model-926");
    await user.type(screen.getByLabelText("图片提示词"), "旧scope迟到不会释放新操作");
    await user.click(screen.getByRole("button", { name: "提交图片任务" }));
    expect(await screen.findByText("第 1 项 · 提交中")).toBeInTheDocument();

    rerender(<MediaGenerationPage {...props} userId="actor-scope-new-1067" />);
    const submitButton = await screen.findByRole("button", { name: "提交图片任务" });
    await waitFor(() => expect(submitButton).toBeEnabled());
    await user.click(submitButton);
    await screen.findByText("第 1 项 · 提交中");
    expect(submitImage).toHaveBeenCalledTimes(2);

    finishFirst?.(receipt("old"));
    await waitFor(() => expect(screen.getByRole("button", { name: "提交图片任务" })).toBeDisabled());
    finishSecond?.(receipt("new"));
    expect(await screen.findByText("图片任务已受理：queued · 服务报价 1 积分 · 计费状态 charged。"))
      .toBeInTheDocument();
    expect(screen.getByText("scope-task-new")).toBeInTheDocument();
  });

  it("ignores an old-scope lookup 401 while keeping a new lookup busy", async () => {
    const user = userEvent.setup();
    let submission = 0;
    const submitImage = vi.fn<MediaGenerationRun["submitImage"]>(async () => {
      submission += 1;
      throw new MediaGenerationError("unknown-outcome", `第${submission}次响应未知`);
    });
    const receipt = (suffix: string) => ({
      operation: "image.single" as const,
      submissionId: `lookup-scope-submission-${suffix}`,
      taskId: `lookup-scope-task-${suffix}`,
      messageId: null,
      status: "queued" as const,
      quotedAmount: 1,
      billingStatus: "charged" as const,
    });
    const lookupResolvers: Array<(receipt: Awaited<ReturnType<MediaGenerationRun["lookup"]>>) => void> = [];
    const lookupRejectors: Array<(error: unknown) => void> = [];
    const lookup = vi.fn<MediaGenerationRun["lookup"]>(() => new Promise((resolve, reject) => {
      lookupResolvers.push(resolve);
      lookupRejectors.push(reject);
    }));
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })), undefined, submitImage, lookup),
    };
    const onUnauthorized = vi.fn();
    const props = { services, sessionGeneration: 1, onUnauthorized, onOpenTasks: vi.fn() };
    const { rerender } = render(<MediaGenerationPage {...props} userId="actor-lookup-old-1067" />);

    await user.click(screen.getByRole("button", { name: "普通图片" }));
    await user.selectOptions(await screen.findByLabelText("图片模型"), "image-model-926");
    await user.type(screen.getByLabelText("图片提示词"), "按原scope核对未知结果");
    await user.click(screen.getByRole("button", { name: "提交图片任务" }));
    await waitFor(() => expect(lookup).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "正在核对…" })).toBeDisabled();

    rerender(<MediaGenerationPage {...props} userId="actor-lookup-new-1067" />);
    const submitButton = await screen.findByRole("button", { name: "提交图片任务" });
    await waitFor(() => expect(submitButton).toBeEnabled());
    await user.click(submitButton);
    await waitFor(() => expect(lookup).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "正在核对…" })).toBeDisabled();

    const unauthorizedCallsBeforeStaleLookup = onUnauthorized.mock.calls.length;
    lookupRejectors[0]?.(new ApiError("http", "Unauthorized", 401));
    await waitFor(() => expect(screen.getByRole("button", { name: "正在核对…" })).toBeDisabled());
    await Promise.resolve();
    await Promise.resolve();
    expect(onUnauthorized).toHaveBeenCalledTimes(unauthorizedCallsBeforeStaleLookup);
    expect(screen.queryByRole("button", { name: "重新核对提交" })).not.toBeInTheDocument();
    lookupResolvers[1]?.(receipt("new"));
    expect(await screen.findByText(/1 项图片任务已按顺序受理/u)).toBeInTheDocument();
    expect(screen.getByText("lookup-scope-task-new")).toBeInTheDocument();
  });

  it("stops after an unknown second image and resumes that same key/body without replaying the first", async () => {
    const user = userEvent.setup();
    const receipt = (index: number) => ({
      operation: "image.single" as const,
      submissionId: `recovery-submission-${index}`,
      taskId: `recovery-task-${index}`,
      messageId: null,
      status: "queued" as const,
      quotedAmount: 1,
      billingStatus: "charged" as const,
    });
    const submitImage = vi.fn<MediaGenerationRun["submitImage"]>()
      .mockResolvedValueOnce(receipt(1))
      .mockRejectedValueOnce(new MediaGenerationError("unknown-outcome", "第二项响应超时"))
      .mockResolvedValueOnce(receipt(2))
      .mockResolvedValueOnce(receipt(3));
    const lookup = vi.fn<MediaGenerationRun["lookup"]>(async () => {
      throw new MediaGenerationError("unknown-outcome", "仍无核对回执");
    });
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })), undefined, submitImage, lookup),
    };
    render(<MediaGenerationPage services={services} userId="actor-926" sessionGeneration={4}
      onUnauthorized={vi.fn()} onOpenTasks={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "普通图片" }));
    await user.selectOptions(await screen.findByLabelText("图片模型"), "image-model-926");
    await user.type(screen.getByLabelText("图片提示词"), "恢复时不重算的正文");
    await user.selectOptions(screen.getByLabelText("生成数量"), "3");
    await user.click(screen.getByRole("button", { name: "提交图片任务" }));

    await screen.findByText("提交状态尚未确认。页面保留了本次请求，请先核对状态再继续。");
    expect(submitImage).toHaveBeenCalledTimes(2);
    expect(lookup).toHaveBeenCalledOnce();
    expect(lookup.mock.calls[0]?.[0]).toEqual({
      operation: "image.single",
      idempotencyKey: submitImage.mock.calls[1]?.[0].idempotencyKey,
    });
    expect(submitImage.mock.calls[1]?.[0].idempotencyKey).not.toBe(submitImage.mock.calls[0]?.[0].idempotencyKey);

    await user.click(screen.getByRole("button", { name: /按原请求重试本项/u }));
    expect(await screen.findByText(/3 项图片任务已按顺序受理/u)).toBeInTheDocument();
    expect(submitImage).toHaveBeenCalledTimes(4);
    expect(submitImage.mock.calls[1]?.[0]).toBe(submitImage.mock.calls[2]?.[0]);
    expect(submitImage.mock.calls[0]?.[0].idempotencyKey).not.toBe(submitImage.mock.calls[3]?.[0].idempotencyKey);
    expect(submitImage.mock.calls[1]?.[0].prompt).toBe("恢复时不重算的正文");
    expect(submitImage.mock.calls[3]?.[0].prompt).toBe("恢复时不重算的正文");
    expect(submitImage.mock.calls[3]?.[0].idempotencyKey).not.toBe(submitImage.mock.calls[1]?.[0].idempotencyKey);
    expect(screen.getByText(/第 1 项 · 已受理/u)).toBeInTheDocument();
    expect(screen.getByText(/第 2 项 · 已受理/u)).toBeInTheDocument();
    expect(screen.getByText(/第 3 项 · 已受理/u)).toBeInTheDocument();
  });

  it("keeps an unknown group across a same-owner services refresh and checks its original key", async () => {
    const user = userEvent.setup();
    const receipt = (index: number) => ({
      operation: "image.single" as const,
      submissionId: `refresh-submission-${index}`,
      taskId: `refresh-task-${index}`,
      messageId: null,
      status: "queued" as const,
      quotedAmount: 1,
      billingStatus: "charged" as const,
    });
    let resolveOldLookup: ((result: Awaited<ReturnType<MediaGenerationRun["lookup"]>>) => void) | undefined;
    const oldSubmitImage = vi.fn<MediaGenerationRun["submitImage"]>()
      .mockResolvedValueOnce(receipt(1))
      .mockRejectedValueOnce(new MediaGenerationError("unknown-outcome", "第二项提交结果未知"));
    const oldLookup = vi.fn<MediaGenerationRun["lookup"]>(() => new Promise((resolve) => {
      resolveOldLookup = resolve;
    }));
    const newSubmitImage = vi.fn<MediaGenerationRun["submitImage"]>(async () => receipt(3));
    const newLookup = vi.fn<MediaGenerationRun["lookup"]>(async () => receipt(2));
    const oldRun: MediaGenerationRun = {
      isCurrent: () => true,
      submitImage: oldSubmitImage,
      submitVideo: vi.fn(async () => { throw new Error("Unexpected video submit."); }),
      submitBatch: vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })),
      lookup: oldLookup,
    };
    const newRun: MediaGenerationRun = {
      isCurrent: () => true,
      submitImage: newSubmitImage,
      submitVideo: vi.fn(async () => { throw new Error("Unexpected video submit."); }),
      submitBatch: vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })),
      lookup: newLookup,
    };
    const baseServices = createDemoServices(window.localStorage);
    const services: WorkspaceServices = {
      ...baseServices,
      creatorSettings: creatorSettings(),
      mediaGeneration: { beginRun: vi.fn(() => oldRun) },
    };
    const refreshedServices: WorkspaceServices = {
      ...services,
      mediaGeneration: { beginRun: vi.fn(() => newRun) },
    };
    const props = { userId: "actor-services-refresh-1067", sessionGeneration: 1, onUnauthorized: vi.fn(), onOpenTasks: vi.fn() };
    const { rerender } = render(<MediaGenerationPage {...props} services={services} />);

    await user.click(screen.getByRole("button", { name: "普通图片" }));
    await user.selectOptions(await screen.findByLabelText("图片模型"), "image-model-926");
    await user.type(screen.getByLabelText("图片提示词"), "服务刷新后继续使用同一冻结组");
    await user.selectOptions(screen.getByLabelText("生成数量"), "3");
    await user.click(screen.getByRole("button", { name: "提交图片任务" }));

    await waitFor(() => expect(oldLookup).toHaveBeenCalledOnce());
    expect(screen.getByText("第 1 项 · 已受理")).toBeInTheDocument();
    expect(screen.getByText("第 2 项 · 受理待确认")).toBeInTheDocument();
    expect(screen.getByText("第 3 项 · 因前项停止而未提交")).toBeInTheDocument();
    const firstKey = oldSubmitImage.mock.calls[0]?.[0].idempotencyKey;
    const uncertainKey = oldSubmitImage.mock.calls[1]?.[0].idempotencyKey;
    expect(firstKey).toBeDefined();
    expect(uncertainKey).toBeDefined();

    rerender(<MediaGenerationPage {...props} services={refreshedServices} />);
    const lookupButton = await screen.findByRole("button", { name: "重新核对提交" });
    await waitFor(() => expect(lookupButton).toBeEnabled());
    expect(screen.getByText("第 1 项 · 已受理")).toBeInTheDocument();
    expect(screen.getByText("第 2 项 · 受理待确认")).toBeInTheDocument();
    expect(screen.getByText("核对若确认本项已受理，后续未提交项目会按原请求继续；已受理项目不会重复提交。"))
      .toBeInTheDocument();
    expect(newLookup).not.toHaveBeenCalled();

    await user.click(lookupButton);
    await waitFor(() => expect(newLookup).toHaveBeenCalledOnce());
    expect(newLookup.mock.calls[0]?.[0]).toEqual({
      operation: "image.single",
      idempotencyKey: uncertainKey,
    });
    expect(await screen.findByText(/3 项图片任务已按顺序受理/u)).toBeInTheDocument();
    expect(oldSubmitImage).toHaveBeenCalledTimes(2);
    expect(newSubmitImage).toHaveBeenCalledOnce();
    expect(newSubmitImage.mock.calls[0]?.[0].idempotencyKey).not.toBe(firstKey);
    expect(newSubmitImage.mock.calls[0]?.[0].idempotencyKey).not.toBe(uncertainKey);
    expect(screen.getByText(/refresh-task-1/u)).toBeInTheDocument();
    expect(screen.getByText(/refresh-task-2/u)).toBeInTheDocument();
    expect(screen.getByText(/refresh-task-3/u)).toBeInTheDocument();

    resolveOldLookup?.(receipt(99));
    await waitFor(() => expect(screen.getByText(/3 项图片任务已按顺序受理/u)).toBeInTheDocument());
    expect(newSubmitImage).toHaveBeenCalledOnce();
  });

  it("keeps an unknown group and offers stop-local-tracking when refreshed services cannot recover it", async () => {
    const user = userEvent.setup();
    let resolveOldLookup: ((result: Awaited<ReturnType<MediaGenerationRun["lookup"]>>) => void) | undefined;
    const receipt = (index: number) => ({
      operation: "image.single" as const,
      submissionId: `unavailable-refresh-submission-${index}`,
      taskId: `unavailable-refresh-task-${index}`,
      messageId: null,
      status: "queued" as const,
      quotedAmount: 1,
      billingStatus: "charged" as const,
    });
    const submitImage = vi.fn<MediaGenerationRun["submitImage"]>()
      .mockResolvedValueOnce(receipt(1))
      .mockRejectedValueOnce(new MediaGenerationError("unknown-outcome", "第二项提交结果未知"));
    const lookup = vi.fn<MediaGenerationRun["lookup"]>(() => new Promise((resolve) => {
      resolveOldLookup = resolve;
    }));
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })), undefined, submitImage, lookup),
    };
    const unavailableServices = (({ mediaGeneration: _mediaGeneration, ...withoutGeneration }) => withoutGeneration)(services);
    const props = { userId: "actor-refresh-unavailable-1067", sessionGeneration: 1, onUnauthorized: vi.fn(), onOpenTasks: vi.fn() };
    const { rerender } = render(<MediaGenerationPage {...props} services={services} />);

    await user.click(screen.getByRole("button", { name: "普通图片" }));
    await user.selectOptions(await screen.findByLabelText("图片模型"), "image-model-926");
    await user.type(screen.getByLabelText("图片提示词"), "保留未知组直到显式停止跟踪");
    await user.selectOptions(screen.getByLabelText("生成数量"), "2");
    await user.click(screen.getByRole("button", { name: "提交图片任务" }));
    await waitFor(() => expect(lookup).toHaveBeenCalledOnce());

    rerender(<MediaGenerationPage {...props} services={unavailableServices} />);
    expect(await screen.findByRole("button", { name: "停止本地跟踪" })).toBeEnabled();
    expect(screen.getByText("第 1 项 · 已受理")).toBeInTheDocument();
    expect(screen.getByText("第 2 项 · 受理待确认")).toBeInTheDocument();
    expect(screen.getByText("当前服务没有媒体生成恢复入口；原请求仍保留，停止本地跟踪前不会继续提交。"))
      .toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "停止本地跟踪" }));
    expect(screen.queryByText("提交状态尚未确认。页面保留了本次请求，请先核对状态再继续。"))
      .not.toBeInTheDocument();
    expect(screen.getByText("第 1 项 · 已受理")).toBeInTheDocument();
    expect(screen.getByText("第 2 项 · 受理待确认")).toBeInTheDocument();
    expect(screen.getByText(/已停止本地跟踪；原提交仍待确认/u)).toBeInTheDocument();
    expect(submitImage).toHaveBeenCalledTimes(2);

    resolveOldLookup?.(receipt(9));
    await waitFor(() => expect(screen.getByText("第 2 项 · 受理待确认")).toBeInTheDocument());
    expect(submitImage).toHaveBeenCalledTimes(2);
  });

  it("preserves an in-flight batch key across a same-owner services refresh and ignores its late receipt", async () => {
    const user = userEvent.setup();
    let finishOldSubmit: ((receipt: Awaited<ReturnType<MediaGenerationRun["submitBatch"]>>) => void) | undefined;
    let finishNewLookup: ((receipt: Awaited<ReturnType<MediaGenerationRun["lookup"]>>) => void) | undefined;
    const receipt = (taskId: string) => ({
      operation: "image.batch" as const,
      tasks: [{ type: "character" as const, assetId: "character-926", assetName: "林舟", messageId: `message-${taskId}`, taskId }],
    });
    const oldSubmitBatch = vi.fn<MediaGenerationRun["submitBatch"]>(() => new Promise((resolve) => {
      finishOldSubmit = resolve;
    }));
    const newSubmitBatch = vi.fn<MediaGenerationRun["submitBatch"]>(async () => ({ operation: "image.batch", tasks: [] }));
    const newLookup = vi.fn<MediaGenerationRun["lookup"]>(() => new Promise((resolve) => {
      finishNewLookup = resolve;
    }));
    const oldRun: MediaGenerationRun = {
      isCurrent: () => true,
      submitImage: vi.fn(async () => { throw new Error("Unexpected image submit."); }),
      submitVideo: vi.fn(async () => { throw new Error("Unexpected video submit."); }),
      submitBatch: oldSubmitBatch,
      lookup: vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })),
    };
    const newRun: MediaGenerationRun = {
      isCurrent: () => true,
      submitImage: vi.fn(async () => { throw new Error("Unexpected image submit."); }),
      submitVideo: vi.fn(async () => { throw new Error("Unexpected video submit."); }),
      submitBatch: newSubmitBatch,
      lookup: newLookup,
    };
    const series = seriesFixture();
    const chapter = { ...chapterFixture(), content: [] };
    const baseServices = createDemoServices(window.localStorage);
    const services: WorkspaceServices = {
      ...baseServices,
      creatorSettings: creatorSettings(),
      mediaGeneration: { beginRun: vi.fn(() => oldRun) },
      listSeries: vi.fn(async () => [series]),
      listChapters: vi.fn(async () => [chapter]),
      listStoryboardAssets: vi.fn(async () => [storyboardFixture()]),
      listCharacters: vi.fn(async () => [characterFixture()]),
      listScenes: vi.fn(async () => []),
      listProps: vi.fn(async () => []),
    };
    const refreshedServices: WorkspaceServices = {
      ...services,
      mediaGeneration: { beginRun: vi.fn(() => newRun) },
    };
    const props = { userId: "actor-batch-refresh-1067", sessionGeneration: 1, onUnauthorized: vi.fn(), onOpenTasks: vi.fn() };
    const { rerender } = render(<MediaGenerationPage {...props} services={services} />);

    await user.click(screen.getByRole("button", { name: "批量素材生图" }));
    await user.selectOptions(await screen.findByLabelText("剧集"), series.id);
    await user.selectOptions(await screen.findByLabelText("章节"), chapter.id);
    const assetChoices = await screen.findByRole("group", { name: /选择生成素材/ });
    await user.click(within(assetChoices).getByRole("checkbox", { name: /林舟/ }));
    await user.click(screen.getByRole("button", { name: "提交 1 项批量任务" }));
    await waitFor(() => expect(oldSubmitBatch).toHaveBeenCalledOnce());
    const frozenInput = oldSubmitBatch.mock.calls[0]?.[0];
    expect(frozenInput).toBeDefined();

    rerender(<MediaGenerationPage {...props} services={refreshedServices} />);
    await waitFor(() => expect(screen.getByLabelText("章节")).toHaveValue(""));
    const lookupButton = await screen.findByRole("button", { name: "重新核对提交" });
    await waitFor(() => expect(lookupButton).toBeEnabled());
    expect(newLookup).not.toHaveBeenCalled();
    expect(newSubmitBatch).not.toHaveBeenCalled();

    await user.click(lookupButton);
    await waitFor(() => expect(newLookup).toHaveBeenCalledOnce());
    expect(newLookup.mock.calls[0]?.[0]).toEqual({
      operation: "image.batch",
      idempotencyKey: frozenInput!.idempotencyKey,
      batchAssets: [{ type: "character", id: "character-926" }],
    });

    finishOldSubmit?.(receipt("late-task"));
    expect(await screen.findByRole("button", { name: "正在核对…" })).toBeDisabled();
    expect(screen.getByText("提交状态尚未确认。页面保留了本次请求，请先核对状态再继续。"))
      .toBeInTheDocument();

    finishNewLookup?.(receipt("current-task"));
    expect(await screen.findByText("已核对原批量请求。批量请求已回读，共 1 项；每项仍可在“我的任务”中查看。"))
      .toBeInTheDocument();
    expect(screen.getByText(/current-task/u)).toBeInTheDocument();
    expect(screen.queryByText(/late-task/u)).not.toBeInTheDocument();
    expect(oldSubmitBatch).toHaveBeenCalledOnce();
    expect(newSubmitBatch).not.toHaveBeenCalled();
  });

  it("reports a current batch lookup 401 after a same-owner services refresh", async () => {
    const user = userEvent.setup();
    let finishOldSubmit: ((receipt: Awaited<ReturnType<MediaGenerationRun["submitBatch"]>>) => void) | undefined;
    const receipt = {
      operation: "image.batch" as const,
      tasks: [{ type: "character" as const, assetId: "character-926", assetName: "林舟", messageId: "late-message", taskId: "late-task" }],
    };
    const oldSubmitBatch = vi.fn<MediaGenerationRun["submitBatch"]>(() => new Promise((resolve) => {
      finishOldSubmit = resolve;
    }));
    const onUnauthorized = vi.fn();
    const newLookup = vi.fn<MediaGenerationRun["lookup"]>(async () => {
      throw new ApiError("http", "Unauthorized", 401);
    });
    const oldRun: MediaGenerationRun = {
      isCurrent: () => true,
      submitImage: vi.fn(async () => { throw new Error("Unexpected image submit."); }),
      submitVideo: vi.fn(async () => { throw new Error("Unexpected video submit."); }),
      submitBatch: oldSubmitBatch,
      lookup: vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })),
    };
    const newRun: MediaGenerationRun = {
      isCurrent: () => true,
      submitImage: vi.fn(async () => { throw new Error("Unexpected image submit."); }),
      submitVideo: vi.fn(async () => { throw new Error("Unexpected video submit."); }),
      submitBatch: vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })),
      lookup: newLookup,
    };
    const series = seriesFixture();
    const chapter = { ...chapterFixture(), content: [] };
    const baseServices = createDemoServices(window.localStorage);
    const services: WorkspaceServices = {
      ...baseServices,
      creatorSettings: creatorSettings(),
      mediaGeneration: { beginRun: vi.fn(() => oldRun) },
      listSeries: vi.fn(async () => [series]),
      listChapters: vi.fn(async () => [chapter]),
      listStoryboardAssets: vi.fn(async () => [storyboardFixture()]),
      listCharacters: vi.fn(async () => [characterFixture()]),
      listScenes: vi.fn(async () => []),
      listProps: vi.fn(async () => []),
    };
    const refreshedServices: WorkspaceServices = {
      ...services,
      mediaGeneration: { beginRun: vi.fn(() => newRun) },
    };
    const props = { userId: "actor-batch-401-refresh-1067", sessionGeneration: 1, onUnauthorized, onOpenTasks: vi.fn() };
    const { rerender } = render(<MediaGenerationPage {...props} services={services} />);

    await user.click(screen.getByRole("button", { name: "批量素材生图" }));
    await user.selectOptions(await screen.findByLabelText("剧集"), series.id);
    await user.selectOptions(await screen.findByLabelText("章节"), chapter.id);
    const assetChoices = await screen.findByRole("group", { name: /选择生成素材/ });
    await user.click(within(assetChoices).getByRole("checkbox", { name: /林舟/ }));
    await user.click(screen.getByRole("button", { name: "提交 1 项批量任务" }));
    await waitFor(() => expect(oldSubmitBatch).toHaveBeenCalledOnce());
    const originalKey = oldSubmitBatch.mock.calls[0]?.[0].idempotencyKey;
    expect(originalKey).toBeDefined();

    rerender(<MediaGenerationPage {...props} services={refreshedServices} />);
    await waitFor(() => expect(screen.getByLabelText("章节")).toHaveValue(""));
    const lookupButton = await screen.findByRole("button", { name: "重新核对提交" });
    await waitFor(() => expect(lookupButton).toBeEnabled());
    const unauthorizedCallsBeforeLookup = onUnauthorized.mock.calls.length;
    await user.click(lookupButton);

    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(unauthorizedCallsBeforeLookup + 1));
    expect(newLookup).toHaveBeenCalledOnce();
    expect(newLookup.mock.calls[0]?.[0]).toEqual({
      operation: "image.batch",
      idempotencyKey: originalKey,
      batchAssets: [{ type: "character", id: "character-926" }],
    });
    expect(oldSubmitBatch).toHaveBeenCalledOnce();
    expect(screen.queryByText(/late-task/u)).not.toBeInTheDocument();

    finishOldSubmit?.(receipt);
    await waitFor(() => expect(screen.queryByText(/late-task/u)).not.toBeInTheDocument());
    expect(onUnauthorized).toHaveBeenCalledTimes(unauthorizedCallsBeforeLookup + 1);
    expect(oldSubmitBatch).toHaveBeenCalledOnce();
  });

  it("does not add a late upload URL after the page session scope changes", async () => {
    const user = userEvent.setup();
    let finishUpload: ((url: string) => void) | undefined;
    const uploadReference = vi.fn<MediaReferenceUploadRun["upload"]>(() => new Promise((resolve) => {
      finishUpload = resolve;
    }));
    const onUnauthorized = vi.fn();
    const onOpenTasks = vi.fn();
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] }))),
      mediaReferenceUpload: referenceUploadPort(uploadReference),
    };
    const props = {
      services,
      userId: "actor-926",
      onUnauthorized,
      onOpenTasks,
    };
    const { rerender } = render(<MediaGenerationPage {...props} sessionGeneration={4} />);

    await user.click(screen.getByRole("button", { name: "普通图片" }));
    await user.upload(screen.getByLabelText("选择图片文件"), new File(["image"], "reference.png", { type: "image/png" }));
    await user.click(screen.getByRole("button", { name: "上传并添加图片到参考列表" }));
    await screen.findByText("正在上传图片…");

    rerender(<MediaGenerationPage {...props} sessionGeneration={5} />);
    finishUpload?.("https://cdn.example.test/late-reference.png");

    const references = screen.getByLabelText("参考图片 URL（每行一项）");
    await waitFor(() => expect(references).toHaveValue(""));
    await waitFor(() => expect(screen.queryByText("正在上传图片…")).not.toBeInTheDocument());
    expect(uploadReference).toHaveBeenCalledOnce();
  });

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
    expect(writeSingleMediaPreference({ apiBaseUrl: services.apiBaseUrl, userId: "actor-926" }, "video", "storyboard", {
      modelId: "video-model-926",
      promptId: "video-prompt-926",
      size: "16:9",
      duration: "10",
      resolution: "720p",
    })).toBe(true);

    render(
      <MediaGenerationPage
        services={services}
        userId="actor-926"
        sessionGeneration={4}
        onUnauthorized={vi.fn()}
        onOpenTasks={vi.fn()}
      />,
    );

    await user.selectOptions(screen.getByLabelText("视频目标"), "asset_message");
    await user.selectOptions(await screen.findByLabelText("剧集"), series.id);
    await user.selectOptions(await screen.findByLabelText("章节"), chapter.id);
    await user.selectOptions(await screen.findByLabelText("素材类型"), "storyboard");
    await user.selectOptions(await screen.findByLabelText("目标素材"), "storyboard-926");
    await waitFor(() => {
      expect(screen.getByLabelText("视频模型")).toHaveValue("video-model-926");
      expect(screen.getByLabelText("提示词目录")).toHaveValue("video-prompt-926");
      expect(screen.getByLabelText("画面比例")).toHaveValue("16:9");
      expect(screen.getByLabelText("时长（秒）")).toHaveValue("10");
      expect(screen.getByLabelText("分辨率")).toHaveValue("720p");
    });
    expect(screen.getByLabelText("视频提示词")).toHaveValue("保持镜头运动连贯\n\n生成自然的镜头延展");
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
    expect(submitVideo.mock.calls[0]![0]).toMatchObject({
      modelId: "video-model-926",
      prompt: "保持镜头运动连贯\n\n生成自然的镜头延展",
      size: "16:9",
      duration: 10,
      resolution: "720p",
    });
  });

  it("keeps an in-flight video request frozen and treats its late result as unknown after a same-owner refresh", async () => {
    const user = userEvent.setup();
    let finishSubmission: ((value: Awaited<ReturnType<MediaGenerationRun["submitVideo"]>>) => void) | undefined;
    const submitVideo = vi.fn<MediaGenerationRun["submitVideo"]>()
      .mockImplementationOnce(() => new Promise((resolve) => {
        finishSubmission = resolve;
      }))
      .mockResolvedValueOnce({
        operation: "video.single",
        submissionId: "video-submission-frozen-retry",
        taskId: "video-task-frozen-retry",
        messageId: "video-message-frozen-retry",
        status: "queued",
        quotedAmount: 3,
        billingStatus: "charged",
      });
    const baseCatalog = creatorSettings();
    let videoCatalogReads = 0;
    const catalogA: CreatorSettingsServicePort = {
      ...baseCatalog,
      listModels: vi.fn(async (category, signal) => {
        const models = await baseCatalog.listModels(category, signal);
        if (category !== "video") return models;
        videoCatalogReads += 1;
        if (videoCatalogReads < 2) return models;
        return models.map((model) => model.id === "video-model-926"
          ? {
            ...model,
            params: JSON.stringify({
              image_size: { enabled: true, default: "16:9", options: ["16:9"] },
              duration: { enabled: true, default: 5, options: [5] },
              resolution: { enabled: true, default: "720p", options: ["720p"] },
            }),
          }
          : model);
      }),
    };
    const servicesA: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: catalogA,
      mediaGeneration: generationPort(vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })), submitVideo),
    };
    const servicesB: WorkspaceServices = {
      ...servicesA,
      creatorSettings: creatorSettings(),
    };
    const props = { userId: "actor-926", sessionGeneration: 4, onUnauthorized: vi.fn(), onOpenTasks: vi.fn() };
    const { rerender } = render(<MediaGenerationPage services={servicesA} {...props} />);

    await user.selectOptions(await screen.findByLabelText("视频模型"), "video-model-926");
    await user.selectOptions(screen.getByLabelText("时长（秒）"), "10");
    await user.selectOptions(screen.getByLabelText("提示词目录"), "video-prompt-926");
    await user.click(screen.getByRole("button", { name: "提交视频任务" }));
    await waitFor(() => expect(submitVideo).toHaveBeenCalledOnce());
    expect(submitVideo.mock.calls[0]![0].duration).toBe(10);

    rerender(<MediaGenerationPage services={servicesB} {...props} />);
    await waitFor(() => expect(screen.queryByText("正在读取剧集、模型和提示词目录…")).not.toBeInTheDocument());
    rerender(<MediaGenerationPage services={servicesA} {...props} />);
    await waitFor(() => expect(videoCatalogReads).toBe(2));
    await waitFor(() => expect(screen.queryByText("正在读取剧集、模型和提示词目录…")).not.toBeInTheDocument());

    finishSubmission?.({
      operation: "video.single",
      submissionId: "video-submission-frozen",
      taskId: "video-task-frozen",
      messageId: "video-message-frozen",
      status: "queued",
      quotedAmount: 3,
      billingStatus: "charged",
    });
    expect(await screen.findByText("第 1 项 · 受理待确认")).toBeInTheDocument();
    rerender(<MediaGenerationPage services={servicesA} {...props} />);
    expect(screen.getByText("第 1 项 · 受理待确认")).toBeInTheDocument();
    expect(submitVideo).toHaveBeenCalledOnce();
    expect(submitVideo.mock.calls[0]![0].duration).toBe(10);
    expect(await screen.findByText("所选时长已不受当前模型支持，请重新选择。", { selector: "p" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新核对提交" })).toBeEnabled();
    expect(screen.getByLabelText("时长（秒）")).toHaveValue("5");

    await user.click(screen.getByRole("button", { name: /按原请求重试本项/u }));
    expect(await screen.findByText(/视频任务已受理/u)).toBeInTheDocument();
    expect(submitVideo).toHaveBeenCalledTimes(2);
    expect(submitVideo.mock.calls[1]?.[0]).toBe(submitVideo.mock.calls[0]?.[0]);
    expect(submitVideo.mock.calls[1]?.[0].duration).toBe(10);
    expect(submitVideo.mock.calls[1]?.[0].idempotencyKey).toBe(submitVideo.mock.calls[0]?.[0].idempotencyKey);
  });

  it("keeps a manual prompt until saved options are explicitly applied and preserves it for a blank saved prompt", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const series = seriesFixture();
    const chapter = chapterFixture();
    const submitImage = vi.fn<MediaGenerationRun["submitImage"]>(async () => ({
      operation: "image.single",
      submissionId: "manual-media-pref-submission",
      taskId: "manual-media-pref-task",
      messageId: "manual-media-pref-message",
      status: "queued",
      quotedAmount: 1,
      billingStatus: "charged",
    }));
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })), undefined, submitImage),
      listSeries: vi.fn(async () => [series]),
      listChapters: vi.fn(async () => [chapter]),
      listStoryboardAssets: vi.fn(async () => [storyboardFixture()]),
      listCharacters: vi.fn(async () => []),
      listScenes: vi.fn(async () => []),
      listProps: vi.fn(async () => []),
    };
    expect(writeSingleMediaPreference({ apiBaseUrl: services.apiBaseUrl, userId: "actor-926" }, "image", "frame", {
      modelId: "image-model-scene-926",
      promptId: "",
      size: "unsupported-size",
    })).toBe(true);
    render(<MediaGenerationPage services={services} userId="actor-926" sessionGeneration={4}
      onUnauthorized={vi.fn()} onOpenTasks={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "普通图片" }));
    await user.type(screen.getByLabelText("图片提示词"), "保留手工描述");
    await user.selectOptions(screen.getByLabelText("图片目标"), "frame_message");
    const applyButton = await screen.findByRole("button", { name: "应用已记住的图片选项" });
    expect(screen.getByLabelText("图片模型")).toHaveValue("");
    expect(screen.getByLabelText("图片提示词")).toHaveValue("保留手工描述");

    await user.click(applyButton);
    expect(screen.getByLabelText("图片模型")).toHaveValue("image-model-scene-926");
    expect(screen.getByLabelText("提示词目录")).toHaveValue("");
    expect(screen.getByLabelText("图片提示词")).toHaveValue("保留手工描述");
    expect(screen.getByLabelText("图片尺寸")).toHaveValue("2:3");
    expect(await screen.findByText(/记住的尺寸已不受当前模型支持/u)).toBeInTheDocument();
    expect(confirm).not.toHaveBeenCalled();

    await user.selectOptions(await screen.findByLabelText("剧集"), series.id);
    await user.selectOptions(await screen.findByLabelText("章节"), chapter.id);
    await user.selectOptions(await screen.findByLabelText("分镜目标"), "storyboard-926");
    await user.click(screen.getByRole("button", { name: "提交图片任务" }));
    await screen.findByText(/图片任务已受理/u);
    expect(submitImage.mock.calls[0]![0]).toMatchObject({
      modelId: "image-model-scene-926",
      prompt: "保留手工描述",
      size: "2:3",
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
    const sharedReference = "https://cdn.example.test/shared-reference.png";
    const chapter = {
      ...chapterFixture(),
      content: [{
        storyboard: ["storyboard-926"],
        character: ["character-926"],
        scene: ["scene-926"],
        prop: ["missing-prop-926"],
        text: "章节分镜正文优先",
      }],
    };
    const scene = { id: "scene-926", series_id: series.id, title: "雨巷", description: "青石路面", image_url: sharedReference, aliases: null, canonical_key: null, created_at: "2026-10-20T00:00:00Z", updated_at: "2026-10-20T00:00:00Z" };
    const character = { ...characterFixture(), image_url: sharedReference };
    const preferences = userPreferencesPort({
      another_preference: { keep: true },
      batch_gen_prefs: {
        character: { model: "image-model-scene-926", prompt: "image-prompt-926", size: "4:3", custom_character_field: "keep-character" },
        scene: { model: "image-model-926", prompt: "image-prompt-scene-926", size: "16:9", custom_scene_field: "keep-scene" },
        prop: { model: "removed-model", prompt: "removed-prompt", size: "64:64", custom_prop_field: "keep-prop" },
        storyboard: { model: "image-model-scene-926", prompt: "image-prompt-storyboard-926", size: "2:3" },
        checks: { character: true, scene: true, prop: false, keep_unknown: "keep-check" },
        extension: { keep: "unknown-batch-field" },
      },
    });
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      mode: "api",
      apiBaseUrl: "http://127.0.0.1:4175/api",
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(submitBatch),
      userPreferences: preferences.port,
      listSeries: vi.fn(async () => [series]),
      listChapters: vi.fn(async () => [chapter]),
      listStoryboardAssets: vi.fn(async () => [storyboardFixture()]),
      listCharacters: vi.fn(async () => [character]),
      listScenes: vi.fn(async () => [scene]),
      listProps: vi.fn(async () => []),
    };

    const props = {
      services,
      userId: "actor-926",
      sessionGeneration: 4,
      onUnauthorized: vi.fn(),
      onOpenTasks,
    };
    const { rerender } = render(<MediaGenerationPage {...props} />);

    await user.click(screen.getByRole("button", { name: "批量素材生图" }));
    await screen.findByText("已读取账户批量选项；部分保存值在当前目录中不可用，请重新选择后保存。");
    await user.selectOptions(screen.getByLabelText("剧集"), series.id);
    await user.selectOptions(await screen.findByLabelText("章节"), chapter.id);
    await user.type(screen.getByLabelText("公共参考图片 URL（每行一项）"), sharedReference);
    expect(screen.getByLabelText("角色模型")).toHaveValue("image-model-scene-926");
    expect(screen.getByLabelText("角色图片尺寸")).toHaveValue("4:3");
    expect(screen.getByLabelText("场景模型")).toHaveValue("image-model-926");
    expect(screen.getByLabelText("场景图片尺寸")).toHaveValue("16:9");
    expect(screen.getByLabelText("场景提示词")).toHaveValue("image-prompt-scene-926");
    expect(screen.getByLabelText("道具模型")).toHaveValue("");
    expect(screen.getByText("已保存的模型不在当前模型目录中，请重新选择。")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("角色模型"), "image-model-926");
    await user.selectOptions(screen.getByLabelText("角色图片尺寸"), "16:9");
    await user.selectOptions(screen.getByLabelText("角色提示词"), "");
    await user.selectOptions(screen.getByLabelText("场景模型"), "image-model-scene-926");
    await user.selectOptions(screen.getByLabelText("场景图片尺寸"), "4:3");
    await user.selectOptions(screen.getByLabelText("场景提示词"), "");
    await user.selectOptions(screen.getByLabelText("场景提示词"), "image-prompt-scene-926");
    await user.selectOptions(screen.getByLabelText("分镜模型"), "image-model-926");
    await user.selectOptions(screen.getByLabelText("分镜图片尺寸"), "1:1");
    await user.selectOptions(screen.getByLabelText("分镜提示词"), "");
    await user.selectOptions(screen.getByLabelText("分镜提示词"), "image-prompt-storyboard-926");

    const listModels = vi.mocked(services.creatorSettings!.listModels);
    const listPrompts = vi.mocked(services.creatorSettings!.listPrompts);
    const modelReadCount = listModels.mock.calls.length;
    const promptReadCount = listPrompts.mock.calls.length;
    rerender(<MediaGenerationPage {...props} onUnauthorized={vi.fn()} />);
    expect(listModels).toHaveBeenCalledTimes(modelReadCount);
    expect(listPrompts).toHaveBeenCalledTimes(promptReadCount);
    expect(screen.getByLabelText("角色模型")).toHaveValue("image-model-926");
    expect(screen.getByLabelText("角色图片尺寸")).toHaveValue("16:9");
    expect(screen.getByLabelText("角色提示词")).toHaveValue("");
    expect(screen.getByLabelText("场景模型")).toHaveValue("image-model-scene-926");
    expect(screen.getByLabelText("场景图片尺寸")).toHaveValue("4:3");
    expect(screen.getByLabelText("分镜图片尺寸")).toHaveValue("1:1");

    await user.click(screen.getByRole("button", { name: "记住批量选项" }));
    await screen.findByText("批量生成选项已保存到当前账号。");
    expect(preferences.update).toHaveBeenCalledOnce();
    expect(preferences.current().another_preference).toEqual({ keep: true });
    expect(preferences.current().batch_gen_prefs).toEqual({
      character: { model: "image-model-926", prompt: "", size: "16:9", custom_character_field: "keep-character" },
      scene: { model: "image-model-scene-926", prompt: "image-prompt-scene-926", size: "4:3", custom_scene_field: "keep-scene" },
      prop: { model: "removed-model", prompt: "removed-prompt", size: "64:64", custom_prop_field: "keep-prop" },
      storyboard: { model: "image-model-926", prompt: "image-prompt-storyboard-926", size: "1:1" },
      checks: { character: true, scene: true, prop: false, keep_unknown: "keep-check" },
      extension: { keep: "unknown-batch-field" },
    });
    const assetChoices = await screen.findByRole("group", { name: /选择生成素材/ });
    await user.click(within(assetChoices).getByRole("checkbox", { name: /第 2 镜 · 雨巷转场镜头/ }));
    await user.click(within(assetChoices).getByRole("checkbox", { name: /^雨巷场景 · 青石路面$/ }));
    await user.click(within(assetChoices).getByRole("checkbox", { name: /林舟/ }));
    await user.click(screen.getByRole("button", { name: "提交 3 项批量任务" }));

    await screen.findByText(/批量请求已回读，共 3 项/);
    expect(submitBatch).toHaveBeenCalledTimes(1);
    const submitted = submitBatch.mock.calls[0]![0];
    expect(submitted).toMatchObject({ chapterId: chapter.id, assets: [
      { type: "storyboard", id: "storyboard-926", name: "雨巷转场镜头", description: "章节分镜正文优先\n\n【角色资产对照】\n图片1 = 林舟\n\n【场景资产对照】\n图片1 = 雨巷", modelId: "image-model-926", promptId: "image-prompt-storyboard-926", size: "1:1", referenceImages: [sharedReference] },
      { type: "scene", id: "scene-926", modelId: "image-model-scene-926", promptId: "image-prompt-scene-926", size: "4:3" },
      { type: "character", id: "character-926", modelId: "image-model-926", promptId: "", size: "16:9" },
    ] });
    expect(submitted.assets[1]!.referenceImages).toEqual([sharedReference]);
    expect(submitted.assets[2]!.referenceImages).toEqual([sharedReference]);
    expect(submitted.assets[0]!.description).not.toContain("missing-prop-926");
    expect(submitted.idempotencyKey).toEqual(expect.any(String));

    await user.click(screen.getByRole("button", { name: "查看我的任务" }));
    expect(onOpenTasks).toHaveBeenCalledTimes(1);

    cleanup();
    render(<MediaGenerationPage {...props} />);
    await user.click(screen.getByRole("button", { name: "批量素材生图" }));
    await screen.findByText("已读取账户批量选项；部分保存值在当前目录中不可用，请重新选择后保存。");
    expect(screen.getByLabelText("角色提示词")).toHaveValue("");
  });

  it("keeps a frozen batch unchanged when account preferences arrive late and isolates a later account", async () => {
    const user = userEvent.setup();
    let finishPreferences: ((value: UserPreferences) => void) | undefined;
    const latePreferences = new Promise<UserPreferences>((resolve) => { finishPreferences = resolve; });
    let finishStalePreferences: ((value: UserPreferences) => void) | undefined;
    const stalePreferences = new Promise<UserPreferences>((resolve) => { finishStalePreferences = resolve; });
    let preferenceRead = 0;
    let finishSubmission: ((value: Awaited<ReturnType<MediaGenerationRun["submitBatch"]>>) => void) | undefined;
    const submitBatch = vi.fn<MediaGenerationRun["submitBatch"]>(() => new Promise((resolve) => { finishSubmission = resolve; }));
    const preferencesA: UserPreferencesPort = {
      get: vi.fn(() => {
        preferenceRead += 1;
        if (preferenceRead === 1) return Promise.resolve({});
        return preferenceRead === 2 ? latePreferences : stalePreferences;
      }),
      update: vi.fn(async () => ({ updatedKeys: ["batch_gen_prefs"] })),
    };
    const preferencesB = userPreferencesPort({
      batch_gen_prefs: {
        character: { model: "image-model-926", prompt: "image-prompt-926", size: "1:1" },
        checks: { character: true },
      },
    });
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(submitBatch),
      userPreferences: preferencesA,
      listSeries: vi.fn(async () => [seriesFixture()]),
      listChapters: vi.fn(async () => [chapterFixture()]),
      listStoryboardAssets: vi.fn(async () => [storyboardFixture()]),
      listCharacters: vi.fn(async () => [characterFixture()]),
      listScenes: vi.fn(async () => []),
      listProps: vi.fn(async () => []),
    };
    const props = { services, userId: "actor-926", sessionGeneration: 4, onUnauthorized: vi.fn(), onOpenTasks: vi.fn() };
    const { rerender } = render(<MediaGenerationPage {...props} />);

    await user.click(screen.getByRole("button", { name: "批量素材生图" }));
    await user.click(await screen.findByLabelText("检查角色图片"));
    await user.selectOptions(await screen.findByLabelText("角色模型"), "image-model-scene-926");
    await user.selectOptions(screen.getByLabelText("剧集"), "series-926");
    await user.selectOptions(await screen.findByLabelText("章节"), "chapter-926");
    const assetChoices = await screen.findByRole("group", { name: /选择生成素材/ });
    await user.click(within(assetChoices).getByRole("checkbox", { name: /林舟/ }));
    await user.click(screen.getByRole("button", { name: "提交 1 项批量任务" }));

    await waitFor(() => expect(submitBatch).toHaveBeenCalledOnce());
    const frozenInput = submitBatch.mock.calls[0]![0];
    expect(frozenInput.assets[0]).toMatchObject({
      type: "character",
      modelId: "image-model-scene-926",
      size: "2:3",
      promptId: "image-prompt-926",
    });
    finishSubmission?.({ operation: "image.batch", tasks: [] });
    const acceptedMessage = "批量请求已回读，共 0 项；每项仍可在“我的任务”中查看。";
    await screen.findByText(acceptedMessage);

    finishPreferences?.({ batch_gen_prefs: {
      character: { model: "image-model-926", prompt: "image-prompt-926", size: "16:9" },
      scene: { model: "image-model-scene-926", prompt: "image-prompt-scene-926", size: "4:3" },
    } });
    await waitFor(() => expect(screen.getByLabelText("角色模型")).toHaveValue("image-model-scene-926"));
    expect(screen.getByLabelText("检查角色图片")).not.toBeChecked();
    expect(screen.getByLabelText("场景模型")).toHaveValue("image-model-926");
    expect(screen.getByText(acceptedMessage)).toBeInTheDocument();
    expect(frozenInput.assets[0]).toMatchObject({ modelId: "image-model-scene-926", size: "2:3" });
    expect(submitBatch).toHaveBeenCalledOnce();
    expect(preferencesA.update).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "普通视频" }));
    await user.click(screen.getByRole("button", { name: "批量素材生图" }));
    await waitFor(() => expect(preferencesA.get).toHaveBeenCalledTimes(3));
    rerender(<MediaGenerationPage {...props} userId="actor-927" services={{ ...services, userPreferences: preferencesB.port }} />);
    await screen.findByText("已读取账户中保存的批量选项。修改后可点击“记住批量选项”。");
    expect(screen.getByLabelText("角色模型")).toHaveValue("image-model-926");
    expect(screen.getByLabelText("角色图片尺寸")).toHaveValue("1:1");
    expect(screen.getByLabelText("检查角色图片")).toBeChecked();
    finishStalePreferences?.({ batch_gen_prefs: {
      character: { model: "image-model-scene-926", prompt: "image-prompt-character-926", size: "4:3" },
      checks: { character: false },
    } });
    await waitFor(() => expect(screen.getByLabelText("角色模型")).toHaveValue("image-model-926"));
    expect(screen.getByLabelText("角色图片尺寸")).toHaveValue("1:1");
    expect(screen.getByLabelText("检查角色图片")).toBeChecked();
  });

  it("does not use another series image to satisfy a storyboard check and keeps selected tasks when disabled", async () => {
    const user = userEvent.setup();
    const submitBatch = vi.fn<MediaGenerationRun["submitBatch"]>(async () => ({ operation: "image.batch", tasks: [] }));
    const series = seriesFixture();
    const chapter = {
      ...chapterFixture(),
      content: [{ storyboard: ["storyboard-926"], character: ["character-926"], text: "分镜原文" }],
    };
    const currentCharacter = characterFixture();
    const foreignCharacter = {
      ...currentCharacter,
      series_id: "other-series",
      image_url: "https://other-series.example.test/character.png",
    };
    const scene = {
      id: "scene-926", series_id: series.id, title: "雨巷", description: "青石路面", image_url: null,
      aliases: null, canonical_key: null, created_at: "2026-10-20T00:00:00Z", updated_at: "2026-10-20T00:00:00Z",
    };
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(submitBatch),
      listSeries: vi.fn(async () => [series]),
      listChapters: vi.fn(async () => [chapter]),
      listStoryboardAssets: vi.fn(async () => [storyboardFixture()]),
      listCharacters: vi.fn(async () => [foreignCharacter, currentCharacter]),
      listScenes: vi.fn(async () => [scene]),
      listProps: vi.fn(async () => []),
    };
    render(<MediaGenerationPage services={services} userId="actor-926" sessionGeneration={4}
      onUnauthorized={vi.fn()} onOpenTasks={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "批量素材生图" }));
    await user.selectOptions(await screen.findByLabelText("剧集"), series.id);
    await user.selectOptions(await screen.findByLabelText("章节"), chapter.id);
    const assetChoices = await screen.findByRole("group", { name: /选择生成素材/ });
    await user.click(within(assetChoices).getByRole("checkbox", { name: /第 2 镜 · 雨巷转场镜头/ }));
    await user.click(within(assetChoices).getByRole("checkbox", { name: /林舟/ }));
    await user.click(within(assetChoices).getByRole("checkbox", { name: /雨巷场景/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("缺少引用图片：林舟");
    expect(screen.getByRole("button", { name: "提交 3 项批量任务" })).toBeDisabled();
    expect(submitBatch).not.toHaveBeenCalled();

    await user.click(screen.getByLabelText("检查角色图片"));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "提交 3 项批量任务" }));
    await screen.findByText(/批量请求已回读/);
    expect(submitBatch).toHaveBeenCalledOnce();
    const submittedAssets = submitBatch.mock.calls[0]![0].assets;
    expect(submittedAssets.map(({ type, id }) => [type, id])).toEqual([
      ["storyboard", "storyboard-926"],
      ["character", "character-926"],
      ["scene", "scene-926"],
    ]);
    expect(submittedAssets[0]!.description).toBe("分镜原文");
    expect(submittedAssets[0]!.referenceImages).toEqual([]);
  });

  it("saves only edited checks and preserves unknown preference fields after an uncertain write", async () => {
    const user = userEvent.setup();
    const stored: UserPreferences = {
      batch_gen_prefs: {
        checks: { character: false, keep_unknown: "preserve" },
        extension: { nested: true },
      },
      another_preference: { keep: true },
    };
    const get = vi.fn(async () => JSON.parse(JSON.stringify(stored)) as UserPreferences);
    const update = vi.fn(async (_patch: UserPreferences) => { throw new Error("write result unknown"); });
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] }))),
      userPreferences: { get, update },
    };
    render(<MediaGenerationPage services={services} userId="actor-926" sessionGeneration={4}
      onUnauthorized={vi.fn()} onOpenTasks={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "批量素材生图" }));
    await screen.findByText("已读取账户中保存的批量选项。修改后可点击“记住批量选项”。");
    expect(screen.getByLabelText("检查角色图片")).not.toBeChecked();
    expect(screen.getByLabelText("检查场景图片")).toBeChecked();
    expect(screen.getByLabelText("检查道具图片")).toBeChecked();
    await user.click(screen.getByLabelText("检查场景图片"));
    await user.click(screen.getByRole("button", { name: "记住批量选项" }));

    await screen.findByText("保存结果仍未确认；已只读核对账户偏好，本页保留修改且不会自动重试。");
    expect(update).toHaveBeenCalledOnce();
    expect(update.mock.calls[0]![0]).toEqual({ batch_gen_prefs: {
      checks: { character: false, keep_unknown: "preserve", scene: false },
      extension: { nested: true },
    } });
    expect(get).toHaveBeenCalledTimes(4);
    expect(screen.getByLabelText("检查场景图片")).not.toBeChecked();
  });

  it("only applies quick references after confirmation and submits protected chapter images explicitly", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const referenceUrl = "/uploads/storyboard/reference.png";
    const characterUrl = "https://cdn.example.test/character.png";
    const stylePrompt: CreatorPrompt = {
      id: "quick-reference-style-1046",
      category: "style",
      name: "分镜风格",
      user_prompt: JSON.stringify({
        storyboard: "水墨线稿风格",
        storyboard_image: referenceUrl,
        general: "通用画风",
      }),
      sort_order: 0,
      user_id: "actor-926",
    };
    const baseCreatorSettings = creatorSettings();
    const listCreatorPrompts = baseCreatorSettings.listPrompts;
    const creatorSettingsWithStyle: CreatorSettingsServicePort = {
      ...baseCreatorSettings,
      listPrompts: vi.fn(async (category, signal) => [
        ...(await listCreatorPrompts(category, signal)),
        stylePrompt,
      ]),
    };
    const chapter = {
      ...chapterFixture(),
      content: [{ storyboard: ["storyboard-926"], character: ["character-926"], text: "", original_text: "镜头一：5.2 秒" }],
    };
    const series = { ...seriesFixture(), style_prompt_id: stylePrompt.id, style_prompt: null };
    const storyboard = { ...storyboardFixture(), image_url: referenceUrl };
    const character = { ...characterFixture(), image_url: characterUrl };
    const submitImage = vi.fn<MediaGenerationRun["submitImage"]>(async () => ({
      operation: "image.single",
      submissionId: "quick-reference-submission",
      taskId: "quick-reference-task",
      messageId: "quick-reference-message",
      status: "queued",
      quotedAmount: 2,
      billingStatus: "charged",
    }));
    const base = createDemoServices(window.localStorage);
    const readImage = vi.fn(async () => new Blob(["synthetic PNG bytes"], { type: "image/png" }));
    const preferences = userPreferencesPort({
      batch_gen_prefs: { extension: "preserved" },
      quick_ref_settings: { scene: false, characterVoice: false, custom_setting: "preserved" },
    });
    const services: WorkspaceServices = {
      ...base,
      mode: "api",
      apiBaseUrl: "http://127.0.0.1:4175/api",
      creatorSettings: creatorSettingsWithStyle,
      mediaGeneration: generationPort(vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })), undefined, submitImage),
      userPreferences: preferences.port,
      canvasData: { ...base.canvasData!, readImage } as NonNullable<WorkspaceServices["canvasData"]>,
      listSeries: vi.fn(async () => [series]),
      listChapters: vi.fn(async () => [chapter]),
      listStoryboardAssets: vi.fn(async () => [storyboard]),
      listCharacters: vi.fn(async () => [character]),
      listScenes: vi.fn(async () => []),
      listProps: vi.fn(async () => []),
    };
    expect(writeSingleMediaPreference({ apiBaseUrl: services.apiBaseUrl, userId: "actor-926" }, "image", "frame", {
      modelId: "image-model-scene-926",
      promptId: "image-prompt-scene-926",
      size: "2:3",
    })).toBe(true);
    render(<MediaGenerationPage services={services} userId="actor-926" sessionGeneration={4}
      onUnauthorized={vi.fn()} onOpenTasks={vi.fn()} />);

    await user.click(screen.getByText("快速引用设置"));
    await screen.findByText("已读取当前账号的快速引用设置。");
    const imageQuickReferenceSettings = within(screen.getByRole("group", { name: "普通图片" }));
    expect(imageQuickReferenceSettings.getByLabelText("场景图片")).not.toBeChecked();
    await user.click(imageQuickReferenceSettings.getByLabelText("场景图片"));
    await user.click(screen.getByRole("button", { name: "保存快速引用设置" }));
    await screen.findByText("快速引用设置已保存到当前账号。");
    expect(preferences.update).toHaveBeenCalledOnce();
    expect(preferences.update.mock.calls[0]![0]).toMatchObject({ quick_ref_settings: {
      scene: false,
      characterVoice: false,
      custom_setting: "preserved",
      image: { scene: true },
      video: { scene: false, characterVoice: false },
    } });
    expect(preferences.current().batch_gen_prefs).toEqual({ extension: "preserved" });
    await user.click(screen.getByRole("button", { name: "普通图片" }));
    await user.selectOptions(screen.getByLabelText("图片模型"), "image-model-926");
    await user.selectOptions(screen.getByLabelText("图片目标"), "frame_message");
    await screen.findByRole("button", { name: "应用已记住的图片选项" });
    expect(screen.getByLabelText("图片模型")).toHaveValue("image-model-926");
    await user.selectOptions(screen.getByLabelText("剧集"), "series-926");
    await user.selectOptions(await screen.findByLabelText("章节"), "chapter-926");
    await user.selectOptions(await screen.findByLabelText("分镜目标"), "storyboard-926");
    await user.selectOptions(screen.getByLabelText("提示词目录"), "image-prompt-926");
    await waitFor(() => expect(screen.getByLabelText("图片提示词")).toHaveValue("保持设定一致\n\n细节清晰"));
    await user.type(screen.getByLabelText("参考图片 URL（每行一项）"), "https://manual.example.test/keep.png");

    await user.click(screen.getByRole("button", { name: "快速引用当前章节" }));
    expect(screen.getByLabelText("图片提示词")).toHaveValue("保持设定一致\n\n细节清晰");
    expect(screen.getByLabelText("提示词目录")).toHaveValue("image-prompt-926");
    expect(screen.getByLabelText("参考图片 URL（每行一项）")).toHaveValue("https://manual.example.test/keep.png");
    expect(submitImage).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "快速引用当前章节" }));
    await screen.findByText("已填入当前章节快速引用；尚未提交生成。");
    expect(readImage).toHaveBeenCalledOnce();
    expect(readImage).toHaveBeenCalledWith(referenceUrl, expect.any(AbortSignal));
    expect((screen.getByLabelText("图片提示词") as HTMLTextAreaElement).value).toContain("镜头一：5.2 秒");
    expect((screen.getByLabelText("图片提示词") as HTMLTextAreaElement).value).toContain("【风格提示词】\n水墨线稿风格");
    expect((screen.getByLabelText("图片提示词") as HTMLTextAreaElement).value).toContain("【风格参考图】");
    expect((screen.getByLabelText("图片提示词") as HTMLTextAreaElement).value).toContain("图片1 = 风格参考");
    expect((screen.getByLabelText("图片提示词") as HTMLTextAreaElement).value).toContain("图片1 = 雨巷转场镜头");
    expect(screen.getByLabelText("提示词目录")).toHaveValue("");
    expect(screen.getByLabelText("参考图片 URL（每行一项）")).toHaveValue("");
    expect(screen.getByText(/已准备 2 张章节参考图/)).toBeInTheDocument();
    expect(screen.queryByText(referenceUrl)).not.toBeInTheDocument();
    expect(submitImage).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "提交图片任务" }));
    await screen.findByText(/图片任务已受理/);
    expect(submitImage).toHaveBeenCalledOnce();
    expect(submitImage.mock.calls[0]![0]).toMatchObject({
      target: { kind: "frame_message", chapter_id: "chapter-926", storyboard_asset_id: "storyboard-926" },
      referenceImages: [expect.stringMatching(/^data:image\/png;base64,/u), characterUrl],
    });
    expect(submitImage.mock.calls[0]![0].prompt).toContain("图片1 = 雨巷转场镜头");
    confirm.mockRestore();
  });

  it("cancels an in-progress quick reference without partially replacing the form", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const resolveReads: Array<(blob: Blob) => void> = [];
    const chapter = {
      ...chapterFixture(),
      content: [{ storyboard: ["storyboard-926"], text: "章节描述" }],
    };
    const base = createDemoServices(window.localStorage);
    const readImage = vi.fn((_url: string, _signal: AbortSignal) => new Promise<Blob>((resolve) => {
      resolveReads.push(resolve);
    }));
    const submitImage = vi.fn<MediaGenerationRun["submitImage"]>(async () => ({
      operation: "image.single",
      submissionId: "cancelled-quick-reference-submission",
      taskId: "cancelled-quick-reference-task",
      messageId: "cancelled-quick-reference-message",
      status: "queued",
      quotedAmount: 1,
      billingStatus: "charged",
    }));
    const services: WorkspaceServices = {
      ...base,
      creatorSettings: creatorSettings(),
      mediaGeneration: generationPort(vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })), undefined, submitImage),
      userPreferences: userPreferencesPort({ quick_ref_settings: {} }).port,
      canvasData: { ...base.canvasData!, readImage } as NonNullable<WorkspaceServices["canvasData"]>,
      listSeries: vi.fn(async () => [seriesFixture()]),
      listChapters: vi.fn(async () => [chapter]),
      listStoryboardAssets: vi.fn(async () => [{ ...storyboardFixture(), image_url: "/uploads/storyboard/reference.png" }]),
      listCharacters: vi.fn(async () => []),
      listScenes: vi.fn(async () => []),
      listProps: vi.fn(async () => []),
    };
    render(<MediaGenerationPage services={services} userId="actor-926" sessionGeneration={4}
      onUnauthorized={vi.fn()} onOpenTasks={vi.fn()} />);
    await user.click(screen.getByText("快速引用设置"));
    await screen.findByText("已读取当前账号的快速引用设置。");
    await user.click(screen.getByRole("button", { name: "普通图片" }));
    await user.selectOptions(screen.getByLabelText("图片目标"), "frame_message");
    await user.selectOptions(screen.getByLabelText("剧集"), "series-926");
    await user.selectOptions(await screen.findByLabelText("章节"), "chapter-926");
    await user.selectOptions(await screen.findByLabelText("分镜目标"), "storyboard-926");
    await user.type(screen.getByLabelText("图片提示词"), "保留这段描述");

    await user.click(screen.getByRole("button", { name: "快速引用当前章节" }));
    await waitFor(() => expect(readImage).toHaveBeenCalledOnce());
    await user.click(screen.getByRole("button", { name: "取消准备" }));
    await screen.findByText("准备已取消；当前提示词和参考列表未更改。");
    expect(screen.getByLabelText("图片模型")).toBeEnabled();
    expect(screen.getByRole("button", { name: "快速引用当前章节" })).toBeEnabled();
    expect(screen.getByLabelText("图片提示词")).toHaveValue("保留这段描述");
    expect(screen.queryByText(/已准备 \d+ 张章节参考图/u)).not.toBeInTheDocument();
    expect(screen.getByLabelText("参考图片 URL（每行一项）")).toHaveValue("");

    await user.click(screen.getByRole("button", { name: "快速引用当前章节" }));
    await waitFor(() => expect(readImage).toHaveBeenCalledTimes(2));
    resolveReads[0]?.(new Blob(["late canceled PNG"], { type: "image/png" }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByRole("button", { name: "取消准备" })).toBeInTheDocument();
    expect(screen.getByLabelText("图片模型")).toBeDisabled();
    expect(screen.getByLabelText("图片提示词")).toHaveValue("保留这段描述");

    await user.click(screen.getByRole("button", { name: "取消准备" }));
    await screen.findByText("准备已取消；当前提示词和参考列表未更改。");
    resolveReads[1]?.(new Blob(["late synthetic PNG"], { type: "image/png" }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByLabelText("图片模型")).toBeEnabled();
    expect(screen.getByLabelText("图片提示词")).toHaveValue("保留这段描述");
    expect(screen.queryByText(/已准备 \d+ 张章节参考图/u)).not.toBeInTheDocument();
    expect(submitImage).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it("requires an explicit choice when account quick reference settings cannot be read", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const chapter = {
      ...chapterFixture(),
      content: [{ storyboard: ["storyboard-926"], text: "当前章节描述" }],
    };
    const preferences = userPreferencesPort({});
    preferences.get.mockRejectedValueOnce(new Error("offline"));
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings: creatorSettings(),
      userPreferences: preferences.port,
      listSeries: vi.fn(async () => [seriesFixture()]),
      listChapters: vi.fn(async () => [chapter]),
      listStoryboardAssets: vi.fn(async () => [{
        ...storyboardFixture(),
        image_url: "https://cdn.example.test/storyboard-reference.png",
      }]),
      listCharacters: vi.fn(async () => []),
      listScenes: vi.fn(async () => []),
      listProps: vi.fn(async () => []),
    };

    render(<MediaGenerationPage services={services} userId="actor-926" sessionGeneration={4}
      onUnauthorized={vi.fn()} onOpenTasks={vi.fn()} />);
    await screen.findByText("无法读取账号快速引用设置。填入前会请你确认是否按本页当前开关继续。");
    await user.click(screen.getByRole("button", { name: "普通图片" }));
    await user.selectOptions(screen.getByLabelText("图片目标"), "frame_message");
    await user.selectOptions(screen.getByLabelText("剧集"), "series-926");
    await user.selectOptions(await screen.findByLabelText("章节"), "chapter-926");
    await user.selectOptions(await screen.findByLabelText("分镜目标"), "storyboard-926");

    await user.click(screen.getByRole("button", { name: "快速引用当前章节" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("本页开关可能仍是默认值"));
    expect(screen.getByLabelText("图片提示词")).toHaveValue("");
    expect(screen.getByLabelText("参考图片 URL（每行一项）")).toHaveValue("");

    confirm.mockReturnValueOnce(true);
    await user.click(screen.getByRole("button", { name: "快速引用当前章节" }));
    await screen.findByText("已填入当前章节快速引用；尚未提交生成。");
    expect((screen.getByLabelText("图片提示词") as HTMLTextAreaElement).value).toContain("当前章节描述");
    expect(screen.getByLabelText("参考图片 URL（每行一项）")).toHaveValue("");
    confirm.mockRestore();
  });
});
