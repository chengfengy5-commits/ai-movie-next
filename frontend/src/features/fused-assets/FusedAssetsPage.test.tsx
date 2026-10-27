import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createDemoServices, createApiServices, type WorkspaceServices } from "../../shared/api/services";
import type { Chapter, Character, FusedAsset, Prop, Series, StoryboardAsset } from "../../shared/api/contracts";
import { API_TOKEN_KEY } from "../../shared/api/storage";
import type { CreatorModel, CreatorPrompt } from "../../shared/api/creatorSettings";
import type { FusedGenerationInput, FusedSubmissionReceipt } from "../../shared/api/fusedGeneration";
import type { FusedGenerationOpenIntent } from "./fusedGenerationEntryIntent";
import type { MediaReferenceKind, MediaReferenceUploadPort } from "../../shared/api/mediaReferenceUpload";
import type { TaskReceipt } from "../../shared/api/taskDetails";
import { ApiError } from "../../shared/api/errors";
import { demoSeries } from "../series/demoSeries";
import { FusedAssetsPage } from "./FusedAssetsPage";
import { fusedGenerationPreferenceKey } from "./fusedGenerationPreferences";
import {
  readFusedGenerationSubmissionRegistry,
  readFusedGenerationSubmissionLocator,
  writeFusedGenerationSubmissionLocator,
  writeFusedGenerationSubmissionRecord,
  type FusedGenerationSubmissionRecord,
} from "./fusedGenerationSubmissions";

const apiBaseUrl = "http://127.0.0.1:4175/api";

function series(id: string, name = "雾港来信"): Series {
  return { ...demoSeries[0]!, id, name, can_enter: true };
}

function fusedAsset(overrides: Partial<FusedAsset> = {}): FusedAsset {
  return {
    id: "fused-asset-1",
    series_id: "series-1",
    member_type: "prop",
    members: [{ kind: "asset", id: "prop-1", name: "旧车票", private_prompt: "不可展示的提示词" }],
    name: "旧车票融合",
    image_url: "/media/fused/asset-1.png",
    size: "1024x1024",
    status: "completed",
    error_message: null,
    created_at: "2026-10-26T00:00:00Z",
    updated_at: "2026-10-26T00:00:00Z",
    ...overrides,
  };
}

function character(overrides: Partial<Character> = {}): Character {
  return {
    id: "character-1",
    series_id: "series-1",
    name: "林原",
    description: "",
    image_url: "https://images.example.test/character.png",
    audio_url: null,
    voice_ref: null,
    aliases: null,
    canonical_key: null,
    gender: null,
    age: null,
    role: null,
    appearance: null,
    created_at: "2026-10-26T00:00:00Z",
    updated_at: "2026-10-26T00:00:00Z",
    ...overrides,
  };
}

function prop(overrides: Partial<Prop> = {}): Prop {
  return {
    id: "prop-1",
    series_id: "series-1",
    name: "旧车票",
    description: "",
    image_url: "https://images.example.test/prop.png",
    aliases: null,
    canonical_key: null,
    created_at: "2026-10-26T00:00:00Z",
    updated_at: "2026-10-26T00:00:00Z",
    ...overrides,
  };
}

function renderPage(
  services: WorkspaceServices,
  currentSeries = series("series-1"),
  currentUserId = "fixture-user",
) {
  return render(
    <FusedAssetsPage
      onBack={vi.fn()}
      onUnauthorized={vi.fn()}
      series={currentSeries}
      services={services}
      sessionGeneration={1}
      userId={currentUserId}
    />,
  );
}

function generationModel(): CreatorModel {
  return {
    id: "image-model-1",
    category: "image",
    name: "安全生图模型",
    provider: "fixture",
    params: JSON.stringify({ image_size: { enabled: true, default: "1024x1024", options: ["1024x1024", "1536x1024"] } }),
    cost_per_call: null,
    sort_order: 0,
    is_system: true,
    user_id: "system",
  };
}

function fusePrompt(): CreatorPrompt {
  return {
    id: "fuse-prompt-1",
    category: "fuse",
    name: "角色与道具融合模板",
    system_prompt: "保持原作画风",
    user_prompt: JSON.stringify({ character: "角色合成正文", prop: "道具合成正文" }),
    sort_order: 0,
    user_id: "system",
  };
}

function taskReceipt(overrides: Partial<TaskReceipt> = {}): TaskReceipt {
  return {
    id: "task-fused-1",
    type: "fused",
    messageId: null,
    status: "completed",
    result: null,
    billingStatus: "charged",
    quotedAmount: 0,
    progress: 100,
    progressMessage: null,
    externalTaskId: null,
    externalProvider: null,
    createdAt: null,
    updatedAt: null,
    ...overrides,
  };
}

function generationServices(overrides: Partial<WorkspaceServices> = {}): WorkspaceServices {
  const demo = createDemoServices(window.localStorage);
  const defaultSubmit = vi.fn(async (_input: FusedGenerationInput) => ({ taskId: "task-fused-1", messageId: null }));
  return {
    ...demo,
    apiBaseUrl,
    listFusedAssets: vi.fn(async () => []),
    listCharacters: vi.fn(async () => [
      character(),
      character({ id: "character-no-image", name: "无图角色", image_url: null }),
      character({ id: "foreign-character", name: "外剧集角色", series_id: "series-foreign" }),
    ]),
    listProps: vi.fn(async () => [prop()]),
    creatorSettings: {
      ...demo.creatorSettings!,
      listModels: vi.fn(async (category) => category === "image" ? [generationModel()] : []),
      listPrompts: vi.fn(async (category) => category === "fuse" ? [fusePrompt()] : []),
    },
    fusedGeneration: { submit: defaultSubmit },
    getTaskReceipt: vi.fn(async () => taskReceipt()),
    ...overrides,
  };
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => { resolve = complete; });
  return { promise, resolve };
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("FusedAssetsPage", () => {
  it("keeps the page mounted and blocks new submissions when the localStorage getter fails during recovery enumeration", async () => {
    const submit = vi.fn(async (_input: FusedGenerationInput) => ({ taskId: "must-not-submit", messageId: null }));
    const services = generationServices({ fusedGeneration: { submit } });
    const storageGetter = vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new DOMException("Storage access denied", "SecurityError");
    });

    try {
      renderPage(services);

      expect(await screen.findByText("浏览器本地融合任务记录不可枚举或读取，暂停新融合提交。")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "提交融合生成" })).toBeDisabled();
      expect(submit).not.toHaveBeenCalled();
    } finally {
      storageGetter.mockRestore();
    }
  });

  it("preserves the selected draft and does not submit when localStorage becomes unavailable after mounting", async () => {
    const user = userEvent.setup();
    const submit = vi.fn(async (_input: FusedGenerationInput) => ({ taskId: "must-not-submit", messageId: null }));
    const services = generationServices({ fusedGeneration: { submit } });
    renderPage(services);

    const characterCheckbox = await screen.findByRole("checkbox", { name: "林原" });
    await waitFor(() => expect(characterCheckbox).toBeEnabled());
    await user.click(characterCheckbox);

    const storageGetter = vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new DOMException("Storage access denied", "SecurityError");
    });

    try {
      await user.click(screen.getByRole("button", { name: "提交融合生成" }));

      await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("浏览器未能保存并确认本次原任务标识，请求未发送。"));
      expect(characterCheckbox).toBeChecked();
      expect(submit).not.toHaveBeenCalled();
    } finally {
      storageGetter.mockRestore();
    }
  });

  it("revalidates a stable frame source and sends the captured proof only after explicit apply", async () => {
    const user = userEvent.setup();
    const frame = {
      storyboard: ["shot-a"],
      text: "林原推开门",
      props: ["钥匙"],
      unknown_metadata: { tone: "cold", order: [1, true] },
      fused: [{ id: "old-fused" }],
    };
    const chapter: Chapter = {
      id: "chapter-source",
      series_id: "series-1",
      title: "第一场",
      content: [
        { storyboard: ["shot-b"], text: "走廊空镜" },
        { ...frame, fused: [{ id: "new-fused" }] },
      ],
      order: 0,
      created_at: "2026-10-28T00:00:00Z",
      updated_at: "2026-10-28T00:00:00Z",
      lock: null,
    };
    const storyboard: StoryboardAsset = {
      id: "shot-a",
      series_id: "series-1",
      chapter_id: "chapter-source",
      frame_index: 1,
      name: "第一场镜头",
      description: null,
      image_url: null,
      created_at: "2026-10-28T00:00:00Z",
      updated_at: "2026-10-28T00:00:00Z",
    };
    const openIntent: FusedGenerationOpenIntent = {
      id: "workspace-entry-1",
      epoch: 1,
      userId: "fixture-user",
      apiBaseUrl,
      seriesId: "series-1",
      sessionGeneration: 1,
      memberType: "character",
      memberAssetIds: [],
      target: {
        chapterId: chapter.id,
        frameIndex: 0,
        storyboardAssetId: storyboard.id,
        expectedFrame: frame,
      },
    };
    const submit = vi.fn(async (_input: FusedGenerationInput) => ({ taskId: "targeted-task", messageId: null }));
    const onIntentConsumed = vi.fn();
    const services = generationServices({
      fusedGeneration: { submit },
      listChapters: vi.fn(async () => [chapter]),
      listStoryboardAssets: vi.fn(async () => [storyboard]),
    });
    render(
      <FusedAssetsPage
        onBack={vi.fn()}
        onIntentConsumed={onIntentConsumed}
        onUnauthorized={vi.fn()}
        openIntent={openIntent}
        series={series("series-1")}
        services={services}
        sessionGeneration={1}
        userId="fixture-user"
      />,
    );

    const applyButton = await screen.findByRole("button", { name: "应用来源到当前草稿" });
    await user.click(applyButton);
    expect(onIntentConsumed).toHaveBeenCalledWith("workspace-entry-1");
    const characterCheckbox = screen.getByRole("checkbox", { name: "林原" });
    expect(characterCheckbox).not.toBeChecked();
    await user.click(characterCheckbox);
    await user.click(screen.getByRole("button", { name: "提交融合生成" }));

    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    expect(submit).toHaveBeenCalledWith(expect.objectContaining({
      chapter_id: "chapter-source",
      frame_index: 1,
      storyboard_asset_id: "shot-a",
      expected_frame: frame,
    }), expect.any(AbortSignal), { idempotencyKey: expect.any(String) });
  });

  it("applies a standalone role seed after checking the current member directory without reading chapters", async () => {
    const user = userEvent.setup();
    const listChapters = vi.fn(async () => []);
    const listStoryboardAssets = vi.fn(async () => []);
    const submit = vi.fn(async (_input: FusedGenerationInput) => ({ taskId: "standalone-task", messageId: null }));
    const onIntentConsumed = vi.fn();
    const openIntent: FusedGenerationOpenIntent = {
      id: "asset-library-entry-1",
      epoch: 1,
      userId: "fixture-user",
      apiBaseUrl,
      seriesId: "series-1",
      sessionGeneration: 1,
      memberType: "character",
      memberAssetIds: ["character-1"],
      target: null,
    };
    const services = generationServices({
      fusedGeneration: { submit },
      listChapters,
      listStoryboardAssets,
    });
    render(
      <FusedAssetsPage
        onBack={vi.fn()}
        onIntentConsumed={onIntentConsumed}
        onUnauthorized={vi.fn()}
        openIntent={openIntent}
        series={series("series-1")}
        services={services}
        sessionGeneration={1}
        userId="fixture-user"
      />,
    );

    const applyButton = await screen.findByRole("button", { name: "应用来源到当前草稿" });
    await waitFor(() => expect(applyButton).toBeEnabled());
    expect(listChapters).not.toHaveBeenCalled();
    expect(listStoryboardAssets).not.toHaveBeenCalled();
    await user.click(applyButton);

    expect(onIntentConsumed).toHaveBeenCalledWith("asset-library-entry-1");
    expect(screen.getByRole("checkbox", { name: "林原" })).toBeChecked();
    expect(screen.queryByLabelText("当前分镜目标")).not.toBeInTheDocument();
    expect(screen.getByText(/目标保持为独立生成/u)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "提交融合生成" }));

    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    expect(submit).toHaveBeenCalledWith(expect.objectContaining({
      chapter_id: null,
      frame_index: null,
    }), expect.any(AbortSignal), { idempotencyKey: expect.any(String) });
    expect(submit.mock.calls[0]?.[0]).not.toHaveProperty("storyboard_asset_id");
    expect(submit.mock.calls[0]?.[0]).not.toHaveProperty("expected_frame");
  });

  it("keeps a stale frame entry blocked instead of changing it into standalone generation", async () => {
    const chapter: Chapter = {
      id: "chapter-source",
      series_id: "series-1",
      title: "第一场",
      content: [{ storyboard: ["shot-b"], text: "另一条分镜" }],
      order: 0,
      created_at: "2026-10-28T00:00:00Z",
      updated_at: "2026-10-28T00:00:00Z",
      lock: null,
    };
    const openIntent: FusedGenerationOpenIntent = {
      id: "stale-frame-entry-1",
      epoch: 1,
      userId: "fixture-user",
      apiBaseUrl,
      seriesId: "series-1",
      sessionGeneration: 1,
      memberType: "character",
      memberAssetIds: ["character-1"],
      target: {
        chapterId: chapter.id,
        frameIndex: 0,
        storyboardAssetId: "shot-a",
        expectedFrame: { storyboard: ["shot-a"], text: "原分镜" },
      },
    };
    const submit = vi.fn(async (_input: FusedGenerationInput) => ({ taskId: "must-not-submit", messageId: null }));
    const services = generationServices({
      fusedGeneration: { submit },
      listChapters: vi.fn(async () => [chapter]),
      listStoryboardAssets: vi.fn(async () => []),
    });
    render(
      <FusedAssetsPage
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
        openIntent={openIntent}
        series={series("series-1")}
        services={services}
        sessionGeneration={1}
        userId="fixture-user"
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("来源分镜ID已缺失或重复");
    expect(screen.queryByRole("button", { name: "应用来源到当前草稿" })).not.toBeInTheDocument();
    expect(submit).not.toHaveBeenCalled();
  });

  it("loads the real fused-assets route, previews only safe media, and rereads a name-only update", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    window.localStorage.setItem(API_TOKEN_KEY, "fused-assets-session");
    let current = fusedAsset({ series_id: "series-1", name: "   " });
    let exists = true;
    const calls: Array<{ url: URL; init: RequestInit }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input));
      calls.push({ url, init });
      if (init.method === "GET" && url.pathname === "/api/series/series-1/fused-assets") {
        return jsonResponse(exists ? [current] : []);
      }
      if (init.method === "GET" && [
        "/api/models",
        "/api/prompts",
        "/api/series/series-1/characters",
        "/api/series/series-1/props",
      ].includes(url.pathname)) return jsonResponse([]);
      if (init.method === "PUT") {
        const body = JSON.parse(String(init.body)) as { name: string };
        current = fusedAsset({ series_id: "series-1", name: body.name });
        return jsonResponse(current);
      }
      if (init.method === "DELETE") {
        exists = false;
        return new Response(null, { status: 204 });
      }
      throw new Error("Unexpected HTTP method.");
    });
    const services = createApiServices(apiBaseUrl, { storage: window.localStorage, fetcher });

    renderPage(services);

    const card = await screen.findByRole("article", { name: "未命名融合素材融合素材" });
    expect(within(card).getByRole("img", { name: "未命名融合素材图片" }))
      .toHaveAttribute("src", "http://127.0.0.1:4175/media/fused/asset-1.png");
    expect(within(card).getByText("旧车票")).toBeInTheDocument();
    expect(within(card).queryByText("不可展示的提示词")).not.toBeInTheDocument();
    expect(within(card).getByText("已完成")).toBeInTheDocument();

    await user.click(within(card).getByRole("button", { name: "改名" }));
    const nameInput = within(card).getByRole("textbox", { name: "素材名称" });
    await user.clear(nameInput);
    await user.type(nameInput, "更新后的融合素材");
    await user.click(within(card).getByRole("button", { name: "保存名称" }));

    expect(await screen.findByText("名称已保存，并已回读当前剧集目录。")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "更新后的融合素材" })).toBeInTheDocument();
    const renameCall = calls.find(({ init }) => init.method === "PUT");
    expect(JSON.parse(String(renameCall?.init.body))).toEqual({ name: "更新后的融合素材" });
    await user.click(within(card).getByRole("button", { name: "删除" }));
    expect(await screen.findByText("融合素材已删除，并已从当前剧集目录确认移除。")).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "更新后的融合素材融合素材" })).not.toBeInTheDocument();
    expect(screen.getByText("当前剧集还没有融合素材。")).toBeInTheDocument();
    expect(calls.filter(({ url }) => url.pathname.includes("fused-assets"))
      .map(({ url, init }) => [init.method, url.pathname])).toEqual([
      ["GET", "/api/series/series-1/fused-assets"],
      ["PUT", "/api/fused-assets/fused-asset-1"],
      ["GET", "/api/series/series-1/fused-assets"],
      ["DELETE", "/api/fused-assets/fused-asset-1"],
      ["GET", "/api/series/series-1/fused-assets"],
    ]);
    const deleteCall = calls.find(({ init }) => init.method === "DELETE");
    expect(deleteCall?.init.body).toBeUndefined();
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("同时清理本剧集中对此融合素材的引用"));
  });

  it("reconciles unknown rename and delete outcomes with only list reads and no automatic write retry", async () => {
    const user = userEvent.setup();
    const listFusedAssets = vi.fn(async () => [fusedAsset()]);
    const updateFusedAsset = vi.fn(async () => { throw new TypeError("connection lost"); });
    const deleteFusedAsset = vi.fn(async () => { throw new TypeError("connection lost"); });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      apiBaseUrl,
      listFusedAssets,
      updateFusedAsset,
      deleteFusedAsset,
    };

    renderPage(services);
    const card = await screen.findByRole("article", { name: "旧车票融合融合素材" });
    await user.click(within(card).getByRole("button", { name: "改名" }));
    const nameInput = within(card).getByRole("textbox", { name: "素材名称" });
    await user.clear(nameInput);
    await user.type(nameInput, "待核对名称");
    await user.click(within(card).getByRole("button", { name: "保存名称" }));

    expect(await screen.findByText("改名结果未知，已只读刷新当前剧集目录。请按当前名称核对；系统不会自动重试。"))
      .toBeInTheDocument();
    expect(listFusedAssets).toHaveBeenCalledTimes(2);
    expect(updateFusedAsset).toHaveBeenCalledTimes(1);
    expect(updateFusedAsset).toHaveBeenCalledWith(
      "series-1",
      "fused-asset-1",
      { name: "待核对名称" },
      expect.any(AbortSignal),
    );

    await user.click(within(card).getByRole("button", { name: "删除" }));
    expect(await screen.findByText("删除结果未知，只读刷新后目录仍显示此素材。请核对后再决定下一步；系统不会自动重复删除。"))
      .toBeInTheDocument();
    expect(listFusedAssets).toHaveBeenCalledTimes(3);
    expect(updateFusedAsset).toHaveBeenCalledTimes(1);
    expect(deleteFusedAsset).toHaveBeenCalledTimes(1);
  });

  it("previews public HTTPS media without a referrer and rejects executable image URLs", async () => {
    const publicImage = fusedAsset({
      id: "public-image",
      name: "公网融合图",
      image_url: "https://cdn.example.test/media/public.png?signature=fixture",
    });
    const unsafeImage = fusedAsset({
      id: "unsafe-image",
      name: "不安全素材",
      image_url: "javascript:alert(1)",
    });
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      apiBaseUrl,
      listFusedAssets: async () => [publicImage, unsafeImage],
    };

    renderPage(services);

    const publicCard = await screen.findByRole("article", { name: "公网融合图融合素材" });
    const image = within(publicCard).getByRole("img", { name: "公网融合图图片" });
    expect(image).toHaveAttribute("src", "https://cdn.example.test/media/public.png?signature=fixture");
    expect(image).toHaveAttribute("referrerpolicy", "no-referrer");
    const unsafeCard = screen.getByRole("article", { name: "不安全素材融合素材" });
    expect(within(unsafeCard).queryByRole("img", { name: "不安全素材图片" })).not.toBeInTheDocument();
    expect(within(unsafeCard).getByRole("img", { name: "不安全素材没有可显示的图片" })).toBeInTheDocument();
  });

  it("ignores a late directory response from the previous series scope", async () => {
    const firstDirectory = deferred<FusedAsset[]>();
    const listFusedAssets = vi.fn((seriesId: string) => (
      seriesId === "series-1"
        ? firstDirectory.promise
        : Promise.resolve([fusedAsset({ id: "series-2-asset", series_id: "series-2", name: "新范围素材" })])
    ));
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      apiBaseUrl,
      listFusedAssets,
    };
    const initialSeries = series("series-1");
    const view = renderPage(services, initialSeries);

    await waitFor(() => expect(listFusedAssets).toHaveBeenCalledTimes(1));
    view.rerender(
      <FusedAssetsPage
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
        series={series("series-2", "第七码头")}
        services={services}
        sessionGeneration={1}
        userId="fixture-user"
      />,
    );

    expect(await screen.findByRole("article", { name: "新范围素材融合素材" })).toBeInTheDocument();
    await act(async () => {
      firstDirectory.resolve([fusedAsset({ name: "旧范围迟到素材" })]);
      await firstDirectory.promise;
    });
    expect(screen.queryByText("旧范围迟到素材")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "新范围素材" })).toBeInTheDocument();
    expect(listFusedAssets).toHaveBeenCalledTimes(2);
  });

  it("submits the selected current-series members and parsed fuse prompt, then verifies the fused task", async () => {
    const user = userEvent.setup();
    const listFusedAssets = vi.fn(async () => []);
    const submit = vi.fn(async (_input: FusedGenerationInput) => ({ taskId: "task-fused-1", messageId: null }));
    const getTaskReceipt = vi.fn(async () => taskReceipt());
    const lookupSubmission = vi.fn(async () => ({
      status: "accepted" as const,
      feeDisposition: "debit" as const,
      feeAmount: 4,
      taskId: "task-fused-1",
      messageId: null,
    }));
    const upload = vi.fn(async (_kind: MediaReferenceKind, _file: File, _signal: AbortSignal) => (
      "https://images.example.test/uploaded-reference.png"
    ));
    const uploadPort: MediaReferenceUploadPort = {
      beginRun(context) {
        return { isCurrent: context.isContextCurrent, upload };
      },
    };
    const services = generationServices({
      listFusedAssets,
      fusedGeneration: { submit, lookupSubmission },
      getTaskReceipt,
      mediaReferenceUpload: uploadPort,
    });

    renderPage(services);
    await waitFor(() => expect(screen.getByRole("button", { name: "提交融合生成" })).toBeEnabled());

    expect(screen.queryByLabelText("外剧集角色")).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "无图角色" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "林原" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "图片尺寸" }), "1536x1024");
    await user.upload(screen.getByLabelText("或上传图片"), new File(["fixture image"], "uploaded-reference.png", { type: "image/png" }));
    await waitFor(() => expect(upload).toHaveBeenCalledOnce());
    await user.click(screen.getByRole("button", { name: "提交融合生成" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "只读核对" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "只读核对" }));

    expect(await screen.findByText("原融合任务已完成；本地恢复记录仍保留。")).toBeInTheDocument();
    expect(submit).toHaveBeenCalledOnce();
    expect(submit).toHaveBeenCalledWith({
      series_id: "series-1",
      member_type: "character",
      members: [
        { kind: "asset", id: "character-1", name: "林原" },
        { kind: "image", url: "https://images.example.test/uploaded-reference.png", name: "uploaded-reference" },
      ],
      model_id: "image-model-1",
      prompt_id: "fuse-prompt-1",
      prompt_system: "保持原作画风",
      prompt_user: "角色合成正文",
      size: "1536x1024",
      chapter_id: null,
      frame_index: null,
      name: null,
    }, expect.any(AbortSignal), {
      idempotencyKey: expect.any(String),
    });
    expect(lookupSubmission).toHaveBeenCalledWith(expect.any(String), expect.any(AbortSignal));
    expect(getTaskReceipt).toHaveBeenCalledWith("task-fused-1", expect.any(AbortSignal));
    expect(listFusedAssets).toHaveBeenCalledTimes(2);

    const preferenceKey = fusedGenerationPreferenceKey("fixture-user", apiBaseUrl, "character");
    const savedPreference = JSON.parse(window.localStorage.getItem(preferenceKey!) ?? "null") as Record<string, unknown>;
    expect(savedPreference).toEqual({ modelId: "image-model-1", promptId: "fuse-prompt-1", size: "1536x1024" });
    expect(JSON.stringify(savedPreference)).not.toContain("uploaded-reference");
    expect(JSON.stringify(savedPreference)).not.toContain("角色合成正文");
  });

  it("restores type-specific preferences into both character and prop generation requests", async () => {
    const user = userEvent.setup();
    const characterPreference = fusedGenerationPreferenceKey("fixture-user", apiBaseUrl, "character")!;
    const propPreference = fusedGenerationPreferenceKey("fixture-user", apiBaseUrl, "prop")!;
    window.localStorage.setItem(characterPreference, JSON.stringify({
      modelId: "image-model-1",
      promptId: "",
      size: "1536x1024",
    }));
    window.localStorage.setItem(propPreference, JSON.stringify({
      modelId: "image-model-1",
      promptId: "fuse-prompt-1",
      size: "1024x1024",
    }));
    let submissionNumber = 0;
    const submit = vi.fn(async (_input: FusedGenerationInput) => {
      submissionNumber += 1;
      return { taskId: `task-fused-${submissionNumber}`, messageId: null };
    });
    const getTaskReceipt = vi.fn(async (taskId: string) => taskReceipt({ id: taskId }));
    let readCount = 0;
    const lookupSubmission = vi.fn(async () => {
      readCount += 1;
      return {
        status: "accepted" as const,
        feeDisposition: "debit" as const,
        feeAmount: 4,
        taskId: `task-fused-${readCount}`,
        messageId: null,
      };
    });
    const services = generationServices({ fusedGeneration: { submit, lookupSubmission }, getTaskReceipt });
    renderPage(services);

    await waitFor(() => expect(screen.getByRole("checkbox", { name: "林原" })).toBeEnabled());
    expect(screen.getByRole("combobox", { name: "融合提示词" })).toHaveValue("");
    expect(screen.getByRole("combobox", { name: "图片尺寸" })).toHaveValue("1536x1024");
    await user.click(screen.getByRole("checkbox", { name: "林原" }));
    await user.click(screen.getByRole("button", { name: "提交融合生成" }));
    await waitFor(() => expect(screen.getAllByRole("button", { name: "只读核对" })).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "只读核对" }));
    await waitFor(() => expect(getTaskReceipt).toHaveBeenCalledWith("task-fused-1", expect.any(AbortSignal)));
    expect(submit).toHaveBeenNthCalledWith(1, expect.objectContaining({
      member_type: "character",
      prompt_id: "",
      prompt_system: null,
      prompt_user: null,
      size: "1536x1024",
    }), expect.any(AbortSignal), { idempotencyKey: expect.any(String) });

    await user.selectOptions(screen.getByRole("combobox", { name: "融合类型" }), "prop");
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "旧车票" })).toBeEnabled());
    expect(screen.getByRole("combobox", { name: "融合提示词" })).toHaveValue("fuse-prompt-1");
    expect(screen.getByRole("combobox", { name: "图片尺寸" })).toHaveValue("1024x1024");
    await user.click(screen.getByRole("checkbox", { name: "旧车票" }));
    await user.click(screen.getByRole("button", { name: "提交新的独立融合任务" }));
    await waitFor(() => expect(screen.getAllByRole("button", { name: "只读核对" })).toHaveLength(2));
    const propSubmission = screen.getByText(/道具 · 独立生成/u).closest("li");
    if (propSubmission === null) throw new Error("道具恢复记录未展示");
    await user.click(within(propSubmission).getByRole("button", { name: "只读核对" }));
    await waitFor(() => expect(getTaskReceipt).toHaveBeenCalledWith("task-fused-2", expect.any(AbortSignal)));
    expect(submit).toHaveBeenNthCalledWith(2, expect.objectContaining({
      member_type: "prop",
      prompt_id: "fuse-prompt-1",
      prompt_system: "保持原作画风",
      prompt_user: "道具合成正文",
      size: "1024x1024",
    }), expect.any(AbortSignal), { idempotencyKey: expect.any(String) });
  });

  it("keeps manually edited per-type preferences when storage writes fail and another tab changes the saved value", async () => {
    const user = userEvent.setup();
    const preferenceKey = fusedGenerationPreferenceKey("fixture-user", apiBaseUrl, "character")!;
    const originalSetItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key.startsWith("haoai:fused-generation:")) throw new Error("preference storage write denied");
      originalSetItem.call(this, key, value);
    });
    const demo = createDemoServices(window.localStorage);
    const secondModel: CreatorModel = { ...generationModel(), id: "image-model-2", name: "第二个生图模型" };
    const services = generationServices({
      creatorSettings: {
        ...demo.creatorSettings!,
        listModels: vi.fn(async (category) => category === "image" ? [generationModel(), secondModel] : []),
        listPrompts: vi.fn(async (category) => category === "fuse" ? [fusePrompt()] : []),
      },
    });
    renderPage(services);

    await waitFor(() => expect(screen.getByRole("checkbox", { name: "林原" })).toBeEnabled());
    const memberType = screen.getByRole("combobox", { name: "融合类型" });
    const model = screen.getByRole("combobox", { name: "生图模型" });
    const prompt = screen.getByRole("combobox", { name: "融合提示词" });
    const size = screen.getByRole("combobox", { name: "图片尺寸" });
    await user.selectOptions(model, "image-model-2");
    await user.selectOptions(size, "1536x1024");
    await user.selectOptions(prompt, "");
    expect(model).toHaveValue("image-model-2");
    expect(size).toHaveValue("1536x1024");
    expect(prompt).toHaveValue("");
    expect(screen.getByText("浏览器没有保存本类型的本地偏好；当前页面选择仍可用于本次请求。")).toBeInTheDocument();
    expect(window.localStorage.getItem(preferenceKey)).toBeNull();

    await user.selectOptions(memberType, "prop");
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "旧车票" })).toBeEnabled());
    await user.selectOptions(screen.getByRole("combobox", { name: "图片尺寸" }), "1536x1024");

    const otherTabPreference = JSON.stringify({
      modelId: "image-model-1",
      promptId: "fuse-prompt-1",
      size: "1024x1024",
    });
    act(() => {
      originalSetItem.call(window.localStorage, preferenceKey, otherTabPreference);
      window.dispatchEvent(new StorageEvent("storage", { key: preferenceKey, newValue: otherTabPreference }));
    });

    await user.selectOptions(memberType, "character");
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "林原" })).toBeEnabled());
    expect(screen.getByRole("combobox", { name: "生图模型" })).toHaveValue("image-model-2");
    expect(screen.getByRole("combobox", { name: "图片尺寸" })).toHaveValue("1536x1024");
    expect(screen.getByRole("combobox", { name: "融合提示词" })).toHaveValue("");
  });

  it("keeps an unknown key and allows a second explicit task with its own key", async () => {
    const user = userEvent.setup();
    const originalSetItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key.startsWith("haoai:fused-generation:")) throw new Error("storage write denied");
      originalSetItem.call(this, key, value);
    });
    const submit = vi.fn(async (_input: FusedGenerationInput): Promise<{ taskId: string; messageId: string | null }> => {
      throw new ApiError("http", "融合生成服务尚未接线", 503);
    });
    const getTaskReceipt = vi.fn(async () => taskReceipt());
    const lookupSubmission = vi.fn(async () => ({
      status: "provider_result_unknown" as const,
      feeDisposition: "debit" as const,
      feeAmount: 4,
      taskId: "task-fused-unknown",
      messageId: null,
    }));
    const services = generationServices({ fusedGeneration: { submit, lookupSubmission }, getTaskReceipt });
    renderPage(services);

    await waitFor(() => expect(screen.getByRole("button", { name: "提交融合生成" })).toBeEnabled());
    await user.selectOptions(screen.getByRole("combobox", { name: "图片尺寸" }), "1536x1024");
    expect(screen.getByText("浏览器没有保存本类型的本地偏好；当前页面选择仍可用于本次请求。")).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "林原" }));
    await user.click(screen.getByRole("button", { name: "提交融合生成" }));

    expect(await screen.findAllByText("融合生成服务暂不可用。当前没有可核对的任务回执；不会自动重新提交生成。")).toHaveLength(2);
    expect(lookupSubmission).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "只读核对" }));
    expect(await screen.findByText(/原任务的生成结果仍在确认中（记录费用 4 积分）/u)).toBeInTheDocument();
    const unknownCard = screen.getByText(/原任务的生成结果仍在确认中（记录费用 4 积分）/u).closest("li");
    if (unknownCard === null) throw new Error("待核对的原任务记录未展示");
    expect(within(unknownCard).getByText("待核对")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交新的独立融合任务" })).toBeEnabled();
    expect(submit).toHaveBeenCalledOnce();
    expect(submit).toHaveBeenCalledWith(expect.objectContaining({ size: "1536x1024" }), expect.any(AbortSignal), {
      idempotencyKey: expect.any(String),
    });
    expect(lookupSubmission).toHaveBeenCalledWith(expect.any(String), expect.any(AbortSignal));
    expect(getTaskReceipt).not.toHaveBeenCalled();

    const firstKey = readFusedGenerationSubmissionRegistry(window.localStorage, {
      userId: "fixture-user",
      apiBaseUrl,
      seriesId: "series-1",
    }).records[0]?.idempotencyKey;
    await user.click(screen.getByRole("button", { name: "只读核对" }));
    await waitFor(() => expect(lookupSubmission).toHaveBeenCalledTimes(2));
    expect(submit).toHaveBeenCalledOnce();
    await user.click(screen.getByRole("button", { name: "清空当前草稿，开始另一条任务" }));
    await user.click(screen.getByRole("checkbox", { name: "林原" }));
    await user.click(screen.getByRole("button", { name: "提交新的独立融合任务" }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(2));
    const registry = readFusedGenerationSubmissionRegistry(window.localStorage, {
      userId: "fixture-user",
      apiBaseUrl,
      seriesId: "series-1",
    });
    expect(registry.records).toHaveLength(2);
    expect(registry.records.map((record) => record.idempotencyKey)).toContain(firstKey);
    expect(registry.records[0]?.idempotencyKey).not.toBe(registry.records[1]?.idempotencyKey);
  });

  it("migrates a saved v1 submission by readback, then restores it with GET only", async () => {
    const user = userEvent.setup();
    const key = "saved-fused-key-1110";
    writeFusedGenerationSubmissionLocator(window.localStorage, {
      version: 1,
      userId: "fixture-user",
      apiBaseUrl,
      seriesId: "series-1",
      memberType: "character",
      operation: "fused.image",
      idempotencyKey: key,
    });
    const submit = vi.fn(async (_input: FusedGenerationInput) => ({ taskId: "unexpected", messageId: null }));
    const lookupSubmission = vi.fn(async () => ({
      status: "provider_result_unknown" as const,
      feeDisposition: "debit" as const,
      feeAmount: 4,
      taskId: "task-fused-unknown",
      messageId: null,
    }));
    renderPage(generationServices({ fusedGeneration: { submit, lookupSubmission } }));

    expect(await screen.findByRole("button", { name: /角色 · 独立生成 · saved-fu/u })).toBeInTheDocument();
    expect(lookupSubmission).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "只读核对" }));
    expect(await screen.findByText(/原任务的生成结果仍在确认中（记录费用 4 积分）/u)).toBeInTheDocument();
    expect(lookupSubmission).toHaveBeenCalledWith(key, expect.any(AbortSignal));
    expect(submit).not.toHaveBeenCalled();
    const restored = readFusedGenerationSubmissionLocator(window.localStorage, {
      userId: "fixture-user",
      apiBaseUrl,
      seriesId: "series-1",
      memberType: "character",
    });
    expect(restored).toMatchObject({ status: "found", locator: { idempotencyKey: key } });
    expect(JSON.stringify(restored)).not.toContain("prompt");

    expect(readFusedGenerationSubmissionLocator(window.localStorage, {
      userId: "fixture-user",
      apiBaseUrl,
      seriesId: "series-1",
      memberType: "character",
    })).toMatchObject({ status: "found", locator: { idempotencyKey: key } });
  });

  it("does not send a generation request unless its original key was saved first", async () => {
    const user = userEvent.setup();
    const originalSetItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key.startsWith("haoai:fused-submission:")) throw new Error("locator storage denied");
      originalSetItem.call(this, key, value);
    });
    const submit = vi.fn(async (_input: FusedGenerationInput) => ({ taskId: "must-not-send", messageId: null }));
    const lookupSubmission = vi.fn(async () => ({
      status: "charged_incomplete" as const,
      feeDisposition: "zero" as const,
      feeAmount: 0,
      taskId: null,
      messageId: null,
    }));
    renderPage(generationServices({ fusedGeneration: { submit, lookupSubmission } }));

    await waitFor(() => expect(screen.getByRole("button", { name: "提交融合生成" })).toBeEnabled());
    await user.click(screen.getByRole("checkbox", { name: "林原" }));
    await user.click(screen.getByRole("button", { name: "提交融合生成" }));

    expect(await screen.findByText("浏览器未能保存并确认本次原任务标识，请求未发送。")).toBeInTheDocument();
    expect(submit).not.toHaveBeenCalled();
    expect(lookupSubmission).not.toHaveBeenCalled();
  });

  it("preserves a damaged v1 slot while allowing a separately persisted new v2 task", async () => {
    const user = userEvent.setup();
    const storageKey = "haoai:fused-submission:v1:fixture-user:http%3A%2F%2F127.0.0.1%3A4175%2Fapi:series-1:character";
    window.localStorage.setItem(storageKey, "not-json");
    const submit = vi.fn(async (_input: FusedGenerationInput) => ({ taskId: "must-not-send", messageId: null }));
    const lookupSubmission = vi.fn(async () => ({
      status: "charged_incomplete" as const,
      feeDisposition: "debit" as const,
      feeAmount: 4,
      taskId: null,
      messageId: null,
    }));
    renderPage(generationServices({ fusedGeneration: { submit, lookupSubmission } }));

    expect(await screen.findByText(/旧任务记录仍保留，部分未能安全迁入任务列表/u)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "提交融合生成" })).toBeEnabled());
    await user.click(screen.getByRole("checkbox", { name: "林原" }));
    await user.click(screen.getByRole("button", { name: "提交融合生成" }));
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    expect(window.localStorage.getItem(storageKey)).toBe("not-json");
    expect(lookupSubmission).not.toHaveBeenCalled();
  });

  it("keeps the displayed key owner and draft when another tab writes a conflicting record", async () => {
    const user = userEvent.setup();
    const key = "cross-tab-original-key";
    const original: FusedGenerationSubmissionRecord = {
      version: 2,
      userId: "fixture-user",
      apiBaseUrl,
      seriesId: "series-1",
      memberType: "character",
      operation: "fused.image",
      idempotencyKey: key,
      target: { kind: "standalone" },
    };
    expect(writeFusedGenerationSubmissionRecord(window.localStorage, original)).toBe(true);
    const pending = deferred<FusedSubmissionReceipt>();
    const lookupSubmission = vi.fn((_key: string, _signal: AbortSignal) => pending.promise);
    const submit = vi.fn(async (_input: FusedGenerationInput) => ({ taskId: "must-not-submit", messageId: null }));
    renderPage(generationServices({ fusedGeneration: { submit, lookupSubmission } }));

    await waitFor(() => expect(screen.getByRole("checkbox", { name: "林原" })).toBeEnabled());
    await user.click(screen.getByRole("checkbox", { name: "林原" }));
    const originalCard = await screen.findByRole("button", { name: /角色 · 独立生成 · cross-ta/u });
    await user.click(screen.getByRole("button", { name: "只读核对" }));
    await waitFor(() => expect(lookupSubmission).toHaveBeenCalledWith(key, expect.any(AbortSignal)));
    const firstSignal = lookupSubmission.mock.calls[0]?.[1];

    const conflicting: FusedGenerationSubmissionRecord = {
      ...original,
      memberType: "prop",
      target: { kind: "frame", chapterId: "chapter-b", storyboardAssetId: "shot-b", frameIndex: 2 },
    };
    expect(writeFusedGenerationSubmissionRecord(window.localStorage, conflicting)).toBe(true);
    window.dispatchEvent(new StorageEvent("storage", {
      key: `haoai:fused-submission:v2:${encodeURIComponent(original.userId)}:${encodeURIComponent(apiBaseUrl)}:${encodeURIComponent(original.seriesId)}:prop:${encodeURIComponent(key)}`,
    }));

    expect(await screen.findByText(/同一原任务标识对应的本地记录不一致/u)).toBeInTheDocument();
    expect(firstSignal?.aborted).toBe(true);
    expect(originalCard).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /道具 · 分镜/u })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "林原" })).toBeChecked();
    expect(submit).not.toHaveBeenCalled();

    await act(async () => {
      pending.resolve({
        status: "charged_incomplete",
        feeDisposition: "debit",
        feeAmount: 4,
        taskId: null,
        messageId: null,
      });
      await pending.promise;
    });
    expect(screen.getByText(/同一原任务标识对应的本地记录不一致/u)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "只读核对" }));
    await waitFor(() => expect(lookupSubmission).toHaveBeenCalledTimes(2));
    expect(lookupSubmission).toHaveBeenLastCalledWith(key, expect.any(AbortSignal));
    expect(screen.getByText(/同一原任务标识对应的本地记录不一致/u)).toBeInTheDocument();
    expect(submit).not.toHaveBeenCalled();
  });

  it("bounds explicit recovery reads and aborts old-scope observations after a scope change", async () => {
    const user = userEvent.setup();
    const records: FusedGenerationSubmissionRecord[] = Array.from({ length: 5 }, (_, index) => ({
      version: 2,
      userId: "fixture-user",
      apiBaseUrl,
      seriesId: "series-1",
      memberType: index % 2 === 0 ? "character" : "prop",
      operation: "fused.image",
      idempotencyKey: ["a-first", "b-second", "c-third", "d-fourth", "e-fifth"][index]!,
      target: { kind: "standalone" },
    }));
    for (const record of records) expect(writeFusedGenerationSubmissionRecord(window.localStorage, record)).toBe(true);

    let inFlight = 0;
    let maximumInFlight = 0;
    const lookupSubmission = vi.fn((_key: string, signal: AbortSignal) => new Promise<FusedSubmissionReceipt>((_resolve, reject) => {
      inFlight += 1;
      maximumInFlight = Math.max(maximumInFlight, inFlight);
      let released = false;
      signal.addEventListener("abort", () => {
        if (released) return;
        released = true;
        inFlight -= 1;
        reject(new DOMException("aborted", "AbortError"));
      }, { once: true });
    }));
    const submit = vi.fn(async (_input: FusedGenerationInput) => ({ taskId: "unused", messageId: null }));
    const services = generationServices({ fusedGeneration: { submit, lookupSubmission } });
    const view = renderPage(services);

    expect(await screen.findAllByRole("button", { name: "只读核对" })).toHaveLength(5);
    expect(lookupSubmission).not.toHaveBeenCalled();
    await user.click(screen.getAllByRole("button", { name: "只读核对" })[0]!);
    await user.click(screen.getAllByRole("button", { name: "只读核对" })[0]!);
    await user.click(screen.getAllByRole("button", { name: "只读核对" })[0]!);
    await waitFor(() => expect(lookupSubmission).toHaveBeenCalledTimes(3));
    expect(maximumInFlight).toBe(3);
    expect(screen.getAllByRole("button", { name: "只读核对" }).every((button) => (button as HTMLButtonElement).disabled)).toBe(true);

    const firstCard = screen.getByRole("button", { name: /角色 · 独立生成 · a-first/u }).closest("li");
    if (firstCard === null) throw new Error("首条恢复记录未展示");
    await user.click(within(firstCard).getByRole("button", { name: "停止观察" }));
    await waitFor(() => expect(inFlight).toBe(2));
    const secondCard = screen.getByRole("button", { name: /道具 · 独立生成 · b-second/u }).closest("li");
    if (secondCard === null) throw new Error("第二条恢复记录未展示");
    expect(within(secondCard).getByRole("button", { name: "停止观察" })).toBeInTheDocument();
    const fourthCard = screen.getByRole("button", { name: /道具 · 独立生成 · d-fourth/u }).closest("li");
    if (fourthCard === null) throw new Error("第四条恢复记录未展示");
    await user.click(within(fourthCard).getByRole("button", { name: "只读核对" }));
    await waitFor(() => expect(lookupSubmission).toHaveBeenCalledTimes(4));
    expect(maximumInFlight).toBe(3);

    view.rerender(
      <FusedAssetsPage
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
        series={series("series-1")}
        services={services}
        sessionGeneration={2}
        userId="other-actor"
      />,
    );
    await waitFor(() => expect(inFlight).toBe(0));
    expect(screen.queryByRole("button", { name: /a-first|b-second|c-third|d-fourth|e-fifth/u })).not.toBeInTheDocument();
    expect(lookupSubmission).toHaveBeenCalledTimes(4);
    expect(submit).not.toHaveBeenCalled();
  });

  it("does not send a selected fuse template whose JSON has no usable current-type content", async () => {
    const user = userEvent.setup();
    const submit = vi.fn(async (_input: FusedGenerationInput) => ({ taskId: "should-not-exist", messageId: null }));
    const base = generationServices({ fusedGeneration: { submit } });
    const services: WorkspaceServices = {
      ...base,
      creatorSettings: {
        ...base.creatorSettings!,
        listPrompts: vi.fn(async () => [{
          ...fusePrompt(),
          system_prompt: null,
          user_prompt: JSON.stringify({ prop: "只有道具正文" }),
        }]),
      },
    };
    renderPage(services);

    await waitFor(() => expect(screen.getByRole("button", { name: "提交融合生成" })).toBeEnabled());
    await user.click(screen.getByRole("checkbox", { name: "林原" }));
    await user.click(screen.getByRole("button", { name: "提交融合生成" }));

    expect(await screen.findByText(/没有可用于当前融合类型的提示词内容/u)).toBeInTheDocument();
    expect(submit).not.toHaveBeenCalled();
  });

  it("stops one key's observation without cancelling the task and ignores its late response", async () => {
    const user = userEvent.setup();
    const firstRead = deferred<TaskReceipt>();
    const secondRead = deferred<TaskReceipt>();
    const submit = vi.fn(async (_input: FusedGenerationInput, _signal: AbortSignal) => {
      return { taskId: "task-first", messageId: null };
    });
    let taskReadCount = 0;
    const getTaskReceipt = vi.fn(() => {
      taskReadCount += 1;
      return taskReadCount === 1 ? firstRead.promise : secondRead.promise;
    });
    const lookupSubmission = vi.fn(async () => ({
      status: "accepted" as const,
      feeDisposition: "debit" as const,
      feeAmount: 4,
      taskId: "task-first",
      messageId: null,
    }));
    const services = generationServices({ fusedGeneration: { submit, lookupSubmission }, getTaskReceipt });
    renderPage(services);

    await waitFor(() => expect(screen.getByRole("button", { name: "提交融合生成" })).toBeEnabled());
    await user.click(screen.getByRole("checkbox", { name: "林原" }));
    await user.click(screen.getByRole("button", { name: "提交融合生成" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "只读核对" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "只读核对" }));
    await waitFor(() => expect(getTaskReceipt).toHaveBeenCalledWith("task-first", expect.any(AbortSignal)));
    await user.click(screen.getByRole("button", { name: "停止观察" }));
    expect(screen.getByRole("button", { name: "提交新的独立融合任务" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "只读核对" }));
    await waitFor(() => expect(getTaskReceipt).toHaveBeenCalledTimes(2));
    expect(submit).toHaveBeenCalledOnce();
    expect(readFusedGenerationSubmissionRegistry(window.localStorage, {
      userId: "fixture-user",
      apiBaseUrl,
      seriesId: "series-1",
    }).records).toHaveLength(1);

    await act(async () => {
      firstRead.resolve(taskReceipt({ id: "task-first", status: "processing", progress: 12 }));
      await firstRead.promise;
    });
    expect(screen.getByRole("button", { name: "停止观察" })).toBeInTheDocument();

    await act(async () => {
      secondRead.resolve(taskReceipt({ id: "task-first", status: "processing", progress: 25 }));
      await secondRead.promise;
    });
    expect(await screen.findByText("原融合任务状态：生成中。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交新的独立融合任务" })).toBeEnabled();
    expect(submit).toHaveBeenCalledOnce();
  });

  it("ignores a late submit receipt after the actor scope changes", async () => {
    const user = userEvent.setup();
    const pending = deferred<{ taskId: string; messageId: string | null }>();
    const submit = vi.fn((_input: FusedGenerationInput) => pending.promise);
    const getTaskReceipt = vi.fn(async () => taskReceipt());
    const services = generationServices({ fusedGeneration: { submit }, getTaskReceipt });
    const view = renderPage(services);

    await waitFor(() => expect(screen.getByRole("button", { name: "提交融合生成" })).toBeEnabled());
    await user.click(screen.getByRole("checkbox", { name: "林原" }));
    await user.click(screen.getByRole("button", { name: "提交融合生成" }));
    expect(submit).toHaveBeenCalledOnce();

    view.rerender(
      <FusedAssetsPage
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
        series={series("series-1")}
        services={services}
        sessionGeneration={2}
        userId="new-actor"
      />,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "提交融合生成" })).toBeEnabled());
    await act(async () => {
      pending.resolve({ taskId: "late-task", messageId: null });
      await pending.promise;
    });

    expect(getTaskReceipt).not.toHaveBeenCalled();
    expect(screen.queryByText(/任务已受理/u)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交融合生成" })).toBeEnabled();
  });
});
