import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createDemoServices, createApiServices, type WorkspaceServices } from "../../shared/api/services";
import type { Character, FusedAsset, Prop, Series } from "../../shared/api/contracts";
import { API_TOKEN_KEY } from "../../shared/api/storage";
import type { CreatorModel, CreatorPrompt } from "../../shared/api/creatorSettings";
import type { FusedGenerationInput } from "../../shared/api/fusedGeneration";
import type { MediaReferenceKind, MediaReferenceUploadPort } from "../../shared/api/mediaReferenceUpload";
import type { TaskReceipt } from "../../shared/api/taskDetails";
import { ApiError } from "../../shared/api/errors";
import { demoSeries } from "../series/demoSeries";
import { FusedAssetsPage } from "./FusedAssetsPage";
import { fusedGenerationPreferenceKey } from "./fusedGenerationPreferences";
import {
  readFusedGenerationSubmissionLocator,
  writeFusedGenerationSubmissionLocator,
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
      fusedGeneration: { submit },
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

    expect(await screen.findByText("最近任务状态：已完成")).toBeInTheDocument();
    expect(await screen.findByText("融合任务已完成；正在刷新当前剧集目录。")).toBeInTheDocument();
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
    const services = generationServices({ fusedGeneration: { submit }, getTaskReceipt });
    renderPage(services);

    await waitFor(() => expect(screen.getByRole("checkbox", { name: "林原" })).toBeEnabled());
    expect(screen.getByRole("combobox", { name: "融合提示词" })).toHaveValue("");
    expect(screen.getByRole("combobox", { name: "图片尺寸" })).toHaveValue("1536x1024");
    await user.click(screen.getByRole("checkbox", { name: "林原" }));
    await user.click(screen.getByRole("button", { name: "提交融合生成" }));
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
    await user.click(screen.getByRole("button", { name: "提交融合生成" }));

    await waitFor(() => expect(getTaskReceipt).toHaveBeenCalledWith("task-fused-2", expect.any(AbortSignal)));
    expect(submit).toHaveBeenNthCalledWith(2, expect.objectContaining({
      member_type: "prop",
      prompt_id: "fuse-prompt-1",
      prompt_system: "保持原作画风",
      prompt_user: "道具合成正文",
      size: "1024x1024",
    }), expect.any(AbortSignal), { idempotencyKey: expect.any(String) });
  });

  it("keeps an unverified submit frozen and never repeats it after a 503", async () => {
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

    expect(await screen.findByText(/Provider 已派发，结果待核对（记录费用 4 积分）/u)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交融合生成" })).toBeDisabled();
    expect(submit).toHaveBeenCalledOnce();
    expect(submit).toHaveBeenCalledWith(expect.objectContaining({ size: "1536x1024" }), expect.any(AbortSignal), {
      idempotencyKey: expect.any(String),
    });
    expect(lookupSubmission).toHaveBeenCalledWith(expect.any(String), expect.any(AbortSignal));
    expect(getTaskReceipt).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "停止本地跟踪" }));
    expect(await screen.findByText(/服务器上的任务仍可能运行并产生费用/u)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交融合生成" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "提交融合生成" }));
    expect(screen.getByRole("button", { name: "核对原提交回执" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "核对原提交回执" }));
    await waitFor(() => expect(lookupSubmission).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "提交融合生成" })).toBeDisabled();
    expect(submit).toHaveBeenCalledOnce();
    expect(readFusedGenerationSubmissionLocator(window.localStorage, {
      userId: "fixture-user",
      apiBaseUrl,
      seriesId: "series-1",
      memberType: "character",
    })).toMatchObject({ status: "found" });
  });

  it("restores a saved submission by GET only and preserves its locator when tracking stops", async () => {
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

    expect(await screen.findByText(/正在只读查询融合提交回执/u)).toBeInTheDocument();
    expect(await screen.findByText(/Provider 已派发，结果待核对（记录费用 4 积分）/u)).toBeInTheDocument();
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

    await userEvent.setup().click(screen.getByRole("button", { name: "停止本地跟踪" }));
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

    expect(await screen.findByText("浏览器无法保存本次融合提交核对键，未发送请求。")).toBeInTheDocument();
    expect(submit).not.toHaveBeenCalled();
    expect(lookupSubmission).not.toHaveBeenCalled();
  });

  it("blocks generation and preserves an unreadable original locator", async () => {
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

    expect(await screen.findByRole("alert")).toHaveTextContent("浏览器保存的原融合提交定位信息已损坏。为避免丢失未知任务，当前范围暂停新提交；请先恢复这条定位信息。");
    expect(screen.getByRole("button", { name: "提交融合生成" })).toBeDisabled();
    expect(window.localStorage.getItem(storageKey)).toBe("not-json");
    expect(submit).not.toHaveBeenCalled();
    expect(lookupSubmission).not.toHaveBeenCalled();
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

  it("keeps the original locator after stop and guards a newer read from a stale task response", async () => {
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
    await waitFor(() => expect(getTaskReceipt).toHaveBeenCalledWith("task-first", expect.any(AbortSignal)));
    await user.click(screen.getByRole("button", { name: "停止本地跟踪" }));
    expect(screen.getByRole("button", { name: "提交融合生成" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "核对原提交回执" }));
    await waitFor(() => expect(getTaskReceipt).toHaveBeenCalledTimes(2));
    expect(submit).toHaveBeenCalledOnce();
    expect(readFusedGenerationSubmissionLocator(window.localStorage, {
      userId: "fixture-user",
      apiBaseUrl,
      seriesId: "series-1",
      memberType: "character",
    })).toMatchObject({ status: "found", locator: { taskId: "task-first" } });

    await act(async () => {
      firstRead.resolve(taskReceipt({ id: "task-first", status: "processing", progress: 12 }));
      await firstRead.promise;
    });
    const activeReadButtons = screen.getAllByRole("button", { name: "正在核对…" });
    expect(activeReadButtons).toHaveLength(2);
    activeReadButtons.forEach((button) => expect(button).toBeDisabled());

    await act(async () => {
      secondRead.resolve(taskReceipt({ id: "task-first", status: "processing", progress: 25 }));
      await secondRead.promise;
    });
    expect(await screen.findByText("最近任务状态：生成中")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交融合生成" })).toBeDisabled();
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
