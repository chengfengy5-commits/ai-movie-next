import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter, Character, Series, StoryboardAsset } from "../../shared/api/contracts";
import type { MediaGenerationPort, MediaGenerationRun } from "../../shared/api/mediaGeneration";
import type { MediaReferenceUploadPort, MediaReferenceUploadRun } from "../../shared/api/mediaReferenceUpload";
import type { CreatorModel, CreatorPrompt, CreatorSettingsServicePort } from "../../shared/api/creatorSettings";
import type { UserPreferences, UserPreferencesPort } from "../../shared/api/userPreferences";
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
  const prompts: CreatorPrompt[] = [
    prompt,
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
): MediaGenerationPort {
  const run: MediaGenerationRun = {
    isCurrent: () => true,
    submitImage,
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
    await user.selectOptions(await screen.findByLabelText("图片模型"), "image-model-926");
    await user.selectOptions(screen.getByLabelText("提示词目录"), "image-prompt-926");
    await user.selectOptions(screen.getByLabelText("图片目标"), "frame_message");
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
      size: "1:1",
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
    const preferences = userPreferencesPort({
      another_preference: { keep: true },
      batch_gen_prefs: {
        character: { model: "image-model-scene-926", prompt: "image-prompt-926", size: "4:3", custom_character_field: "keep-character" },
        scene: { model: "image-model-926", prompt: "image-prompt-scene-926", size: "16:9", custom_scene_field: "keep-scene" },
        prop: { model: "removed-model", prompt: "removed-prompt", size: "64:64", custom_prop_field: "keep-prop" },
        storyboard: { model: "image-model-scene-926", prompt: "image-prompt-storyboard-926", size: "2:3" },
        checks: { character: false, scene: true, prop: false, keep_unknown: "keep-check" },
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
      listCharacters: vi.fn(async () => [characterFixture()]),
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
      checks: { character: false, scene: true, prop: false, keep_unknown: "keep-check" },
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
      { type: "storyboard", id: "storyboard-926", name: "雨巷转场镜头", description: "石板路上的中景转场", modelId: "image-model-926", promptId: "image-prompt-storyboard-926", size: "1:1" },
      { type: "scene", id: "scene-926", modelId: "image-model-scene-926", promptId: "image-prompt-scene-926", size: "4:3" },
      { type: "character", id: "character-926", modelId: "image-model-926", promptId: "", size: "16:9" },
    ] });
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
      get: vi.fn(() => ++preferenceRead === 1 ? latePreferences : stalePreferences),
      update: vi.fn(async () => ({ updatedKeys: ["batch_gen_prefs"] })),
    };
    const preferencesB = userPreferencesPort({
      batch_gen_prefs: {
        character: { model: "image-model-926", prompt: "image-prompt-926", size: "1:1" },
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
    expect(screen.getByLabelText("场景模型")).toHaveValue("image-model-926");
    expect(screen.getByText(acceptedMessage)).toBeInTheDocument();
    expect(frozenInput.assets[0]).toMatchObject({ modelId: "image-model-scene-926", size: "2:3" });
    expect(submitBatch).toHaveBeenCalledOnce();
    expect(preferencesA.update).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "普通视频" }));
    await user.click(screen.getByRole("button", { name: "批量素材生图" }));
    await waitFor(() => expect(preferencesA.get).toHaveBeenCalledTimes(2));
    rerender(<MediaGenerationPage {...props} userId="actor-927" services={{ ...services, userPreferences: preferencesB.port }} />);
    await screen.findByText("已读取账户中保存的批量选项。修改后可点击“记住批量选项”。");
    expect(screen.getByLabelText("角色模型")).toHaveValue("image-model-926");
    expect(screen.getByLabelText("角色图片尺寸")).toHaveValue("1:1");
    finishStalePreferences?.({ batch_gen_prefs: {
      character: { model: "image-model-scene-926", prompt: "image-prompt-character-926", size: "4:3" },
    } });
    await waitFor(() => expect(screen.getByLabelText("角色模型")).toHaveValue("image-model-926"));
    expect(screen.getByLabelText("角色图片尺寸")).toHaveValue("1:1");
  });
});
