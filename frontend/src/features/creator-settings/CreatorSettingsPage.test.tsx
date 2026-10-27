import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../shared/api/errors";
import { createApiServices } from "../../shared/api/services";
import { API_TOKEN_KEY, type StorageLike } from "../../shared/api/storage";
import type {
  CreatorModel,
  CreatorModelConnectionTestResult,
  CreatorModelCreateInput,
  CreatorModelUpdateInput,
  CreatorPrompt,
  CreatorPromptCreateInput,
  CreatorPromptUpdateInput,
  CreatorSettingsServicePort,
} from "../../shared/api/creatorSettings";
import { parseCreatorModel } from "../../shared/api/creatorSettings";
import { CreatorSettingsPage } from "./CreatorSettingsPage";
import type { ProviderQuotaAccount, ProviderQuotaBalance, ProviderQuotaServicePort } from "../../shared/api/providerQuota";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
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

const ownerId = "creator-a";
const otherOwnerId = "creator-b";

function model(id: string, overrides: Partial<CreatorModel> = {}): CreatorModel {
  return {
    id,
    category: "text",
    name: `模型 ${id}`,
    provider: "fixture-provider",
    model: "fixture-model",
    base_url: null,
    params: "{\"temperature\":0.2}",
    sort_order: 0,
    is_system: false,
    user_id: ownerId,
    ...overrides,
  };
}

function prompt(id: string, overrides: Partial<CreatorPrompt> = {}): CreatorPrompt {
  return {
    id,
    category: "storyboard",
    name: `提示词 ${id}`,
    system_prompt: "系统内容",
    user_prompt: "用户内容",
    sort_order: 0,
    is_system: false,
    user_id: ownerId,
    ...overrides,
  };
}

const openAiImageDefaults = JSON.stringify({
  image_size: { enabled: true, default: "1024x1024", options: ["1024x1024", "1792x1024", "1024x1792"] },
  max_reference_images: { enabled: true, default: 8 },
});

function fillModelDefaults(item: CreatorModel): CreatorModel {
  if (item.category === "image" && item.provider === "openai" && (item.params === null || item.params.trim() === "")) {
    return { ...item, params: openAiImageDefaults };
  }
  return item;
}

function applyUserOrder<T extends { id: string; category: string; sort_order: number }>(
  rows: T[],
  category: string | undefined,
  userOrder: Map<string, string[]>,
): T[] {
  const selected = rows
    .filter((item) => category === undefined || item.category === category)
    .slice()
    .sort((left, right) => left.sort_order - right.sort_order);
  const categories = category === undefined
    ? Array.from(new Set(selected.map((item) => item.category)))
    : [category];
  return categories.flatMap((currentCategory) => {
    const categoryRows = selected.filter((item) => item.category === currentCategory);
    const byId = new Map(categoryRows.map((item) => [item.id, item]));
    const requestedOrder = userOrder.get(currentCategory) ?? [];
    const ordered = requestedOrder.flatMap((id) => {
      const item = byId.get(id);
      return item === undefined ? [] : [item];
    });
    const orderedIds = new Set(requestedOrder);
    return [...ordered, ...categoryRows.filter((item) => !orderedIds.has(item.id))];
  });
}

function makeService() {
  let modelRows: CreatorModel[] = [
    model("system-text", { name: "系统文本模型", is_system: true, user_id: "system", sort_order: 0 }),
    model("owner-text", { name: "我的文本模型", sort_order: 1 }),
    model("other-text", { name: "他人文本模型", user_id: otherOwnerId, sort_order: 2 }),
  ];
  let promptRows: CreatorPrompt[] = [
    prompt("system-storyboard", { name: "系统分镜提示词", is_system: true, user_id: "system", sort_order: 0 }),
    prompt("owner-storyboard", { name: "我的分镜提示词", sort_order: 1 }),
    prompt("other-storyboard", { name: "他人分镜提示词", user_id: otherOwnerId, sort_order: 2 }),
    prompt("system-style", {
      category: "style",
      name: "风格参考",
      system_prompt: "保留未知的系统字段文本",
      user_prompt: '{\n  "video_image": "{{video}}",\n  "unknown": {"keep": true}\n}',
      is_system: true,
      user_id: "system",
      sort_order: 0,
    }),
  ];
  const modelKeys = new Map<string, string>([["system-text", "system-secret-never-render"], ["owner-text", "owner-secret-never-render"]]);
  const modelUserOrders = new Map<string, string[]>();
  const promptUserOrders = new Map<string, string[]>();
  let nextModel = 1;
  let nextPrompt = 1;

  const service: CreatorSettingsServicePort = {
    listModels: vi.fn(async (category, signal) => {
      if (signal.aborted) throw new ApiError("aborted", "aborted");
      return applyUserOrder(modelRows, category, modelUserOrders).map((item) => ({ ...item }));
    }),
    getModel: vi.fn(async (id, signal) => {
      if (signal.aborted) throw new ApiError("aborted", "aborted");
      const found = modelRows.find((item) => item.id === id);
      if (found === undefined) throw new ApiError("http", "not found", 404);
      return { ...found };
    }),
    createModel: vi.fn(async (input: CreatorModelCreateInput, signal) => {
      if (signal.aborted) throw new ApiError("aborted", "aborted");
      const params = input.params ?? null;
      const created = fillModelDefaults(model(`created-model-${nextModel++}`, {
        category: input.category,
        name: input.name,
        provider: input.provider,
        model: input.model ?? null,
        base_url: input.base_url ?? null,
        params,
        sort_order: modelRows.filter((item) => item.category === input.category).length,
        is_system: false,
        user_id: ownerId,
      }));
      modelKeys.set(created.id, input.api_key);
      modelRows = [...modelRows, created];
      return { ...created };
    }),
    updateModel: vi.fn(async (id: string, update: CreatorModelUpdateInput, signal) => {
      if (signal.aborted) throw new ApiError("aborted", "aborted");
      modelRows = modelRows.map((item) => item.id === id
        ? fillModelDefaults({ ...item, ...update, api_key: undefined } as CreatorModel)
        : item);
      if (update.api_key !== undefined) modelKeys.set(id, update.api_key);
      const found = modelRows.find((item) => item.id === id);
      if (found === undefined) throw new ApiError("http", "not found", 404);
      return { ...found };
    }),
    deleteModel: vi.fn(async (id, signal) => {
      if (signal.aborted) throw new ApiError("aborted", "aborted");
      modelRows = modelRows.filter((item) => item.id !== id);
      return { message: "deleted" };
    }),
    reorderModels: vi.fn(async (input, signal) => {
      if (signal.aborted) throw new ApiError("aborted", "aborted");
      const index = new Map<string, number>(input.model_ids.map((id: string, order: number): [string, number] => [id, order]));
      modelUserOrders.set(input.category, [...input.model_ids]);
      modelRows = modelRows.map((item) => !item.is_system
        && item.user_id === ownerId
        && item.category === input.category
        && index.has(item.id)
        ? { ...item, sort_order: index.get(item.id) ?? item.sort_order }
        : item);
      return { message: "ordered" };
    }),
    listPrompts: vi.fn(async (category, signal) => {
      if (signal.aborted) throw new ApiError("aborted", "aborted");
      return applyUserOrder(promptRows, category, promptUserOrders).map((item) => ({ ...item }));
    }),
    getPrompt: vi.fn(async (id, signal) => {
      if (signal.aborted) throw new ApiError("aborted", "aborted");
      const found = promptRows.find((item) => item.id === id && item.is_system === false && item.user_id === ownerId);
      if (found === undefined) throw new ApiError("http", "not found", 404);
      const { is_system: _isSystem, ...detail } = found;
      return { ...detail };
    }),
    createPrompt: vi.fn(async (input: CreatorPromptCreateInput, signal) => {
      if (signal.aborted) throw new ApiError("aborted", "aborted");
      const created = prompt(`created-prompt-${nextPrompt++}`, {
        category: input.category,
        name: input.name,
        system_prompt: input.system_prompt ?? null,
        user_prompt: input.user_prompt ?? null,
        sort_order: promptRows.filter((item) => item.category === input.category).length,
        is_system: false,
        user_id: ownerId,
      });
      promptRows = [...promptRows, created];
      const { is_system: _isSystem, ...response } = created;
      return { ...response };
    }),
    updatePrompt: vi.fn(async (id: string, update: CreatorPromptUpdateInput, signal) => {
      if (signal.aborted) throw new ApiError("aborted", "aborted");
      promptRows = promptRows.map((item) => item.id === id ? { ...item, ...update } : item);
      const found = promptRows.find((item) => item.id === id);
      if (found === undefined) throw new ApiError("http", "not found", 404);
      const { is_system: _isSystem, ...response } = found;
      return { ...response };
    }),
    deletePrompt: vi.fn(async (id, signal) => {
      if (signal.aborted) throw new ApiError("aborted", "aborted");
      promptRows = promptRows.filter((item) => item.id !== id);
      return { message: "deleted" };
    }),
    reorderPrompts: vi.fn(async (input, signal) => {
      if (signal.aborted) throw new ApiError("aborted", "aborted");
      const index = new Map<string, number>(input.prompt_ids.map((id: string, order: number): [string, number] => [id, order]));
      promptUserOrders.set(input.category, [...input.prompt_ids]);
      promptRows = promptRows.map((item) => item.is_system !== true
        && item.user_id === ownerId
        && item.category === input.category
        && index.has(item.id)
        ? { ...item, sort_order: index.get(item.id) ?? item.sort_order }
        : item);
      return { message: "ordered" };
    }),
  };

  return {
    service,
    addModel(item: CreatorModel) { modelRows = [...modelRows, item]; },
    addPrompt(item: CreatorPrompt) { promptRows = [...promptRows, item]; },
    modelKeys,
    modelRows: () => modelRows,
    promptRows: () => promptRows,
  };
}

function renderPage(
  service: CreatorSettingsServicePort,
  userId = ownerId,
  isSuperuser = false,
  providerQuotaService?: ProviderQuotaServicePort,
) {
  const onUnauthorized = vi.fn();
  const onBack = vi.fn();
  const view = render(<CreatorSettingsPage
    isSuperuser={isSuperuser}
    onBack={onBack}
    onUnauthorized={onUnauthorized}
    providerQuotaService={providerQuotaService}
    service={service}
    userId={userId}
  />);
  return { onUnauthorized, onBack, view };
}

describe("creator settings", () => {
  it("keeps system and other-user models read-only and sends only changed fields", async () => {
    const user = userEvent.setup();
    const { service } = makeService();
    renderPage(service);

    const personalCard = await screen.findByText("我的文本模型").then((node) => node.closest("article"));
    const systemCard = screen.getByText("系统文本模型").closest("article");
    expect(personalCard).not.toBeNull();
    expect(systemCard).not.toBeNull();
    expect(screen.queryByText("他人文本模型")).not.toBeInTheDocument();
    expect(within(systemCard!).getByRole("button", { name: "编辑" })).toBeDisabled();
    expect(within(systemCard!).getByRole("button", { name: "删除" })).toBeDisabled();
    expect(document.body).not.toHaveTextContent("system-secret-never-render");
    expect(document.body).not.toHaveTextContent("owner-secret-never-render");

    await user.click(within(personalCard!).getByRole("button", { name: "编辑" }));
    const keyInput = await screen.findByLabelText("API 密钥");
    expect(keyInput).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "保存并回读" }));
    expect(service.updateModel).not.toHaveBeenCalled();
    expect(await screen.findByText("没有检测到改动，未发送保存请求。")).toBeInTheDocument();

    await user.click(within(personalCard!).getByRole("button", { name: "编辑" }));
    await user.clear(await screen.findByLabelText("名称"));
    await user.type(screen.getByLabelText("名称"), "更新后的模型");
    await user.click(screen.getByRole("button", { name: "保存并回读" }));
    await waitFor(() => expect(service.updateModel).toHaveBeenCalledTimes(1));
    expect(vi.mocked(service.updateModel).mock.calls[0]?.[1]).toEqual({ name: "更新后的模型" });
    expect(await screen.findByText(/配置已回读确认/)).toBeInTheDocument();
  });

  it("copies a system model into a new personal draft and requires a fresh key", async () => {
    const user = userEvent.setup();
    const { service, modelKeys } = makeService();
    renderPage(service);
    const systemCard = (await screen.findByText("系统文本模型")).closest("article");
    expect(systemCard).not.toBeNull();
    await user.click(within(systemCard!).getByRole("button", { name: "复制" }));
    expect(await screen.findByLabelText("名称")).toHaveValue("系统文本模型 副本");
    expect(screen.getByLabelText("API 密钥")).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "保存并回读" }));
    expect(service.createModel).not.toHaveBeenCalled();
    expect(await screen.findByText(/新建或复制模型需要输入你自己的 API 密钥/)).toBeInTheDocument();

    await user.type(screen.getByLabelText("API 密钥"), "new-owner-key");
    await user.click(screen.getByRole("button", { name: "保存并回读" }));
    await waitFor(() => expect(service.createModel).toHaveBeenCalledTimes(1));
    const input = vi.mocked(service.createModel).mock.calls[0]?.[0];
    expect(input).toMatchObject({ is_system: false, api_key: "new-owner-key", provider: "fixture-provider" });
    expect(JSON.stringify(input)).not.toContain("system-secret-never-render");
    await screen.findByText(/模型非密钥配置已回读确认；密钥变更请求已返回成功/);
    expect(modelKeys.get("created-model-1")).toBe("new-owner-key");
  });

  it("copies a complex style prompt without rewriting its JSON text", async () => {
    const user = userEvent.setup();
    const { service } = makeService();
    renderPage(service);
    await user.click(screen.getByRole("tab", { name: "提示词" }));
    await screen.findByText("系统分镜提示词");
    await user.selectOptions(screen.getByLabelText("提示词分类"), "style");
    const styleCard = (await screen.findByText("风格参考")).closest("article");
    expect(styleCard).not.toBeNull();
    await user.click(within(styleCard!).getByRole("button", { name: "复制" }));
    const originalJson = '{\n  "video_image": "{{video}}",\n  "unknown": {"keep": true}\n}';
    expect(await screen.findByLabelText("用户提示词")).toHaveValue(originalJson);
    await user.click(screen.getByRole("button", { name: "保存并回读" }));
    await waitFor(() => expect(service.createPrompt).toHaveBeenCalledTimes(1));
    expect(vi.mocked(service.createPrompt).mock.calls[0]?.[0]).toMatchObject({
      category: "style",
      name: "风格参考 副本",
      user_prompt: originalJson,
      is_system: false,
    });
    expect(await screen.findByText(/配置已回读确认/)).toBeInTheDocument();
    expect(service.updatePrompt).not.toHaveBeenCalled();
  });

  it("updates and deletes only a listed personal prompt", async () => {
    const user = userEvent.setup();
    const { service } = makeService();
    renderPage(service);
    await user.click(screen.getByRole("tab", { name: "提示词" }));
    const personalCard = (await screen.findByText("我的分镜提示词")).closest("article");
    const systemCard = screen.getByText("系统分镜提示词").closest("article");
    expect(personalCard).not.toBeNull();
    expect(systemCard).not.toBeNull();
    expect(screen.queryByText("他人分镜提示词")).not.toBeInTheDocument();
    expect(within(systemCard!).getByRole("button", { name: "编辑" })).toBeDisabled();
    expect(within(systemCard!).getByRole("button", { name: "删除" })).toBeDisabled();

    await user.click(within(personalCard!).getByRole("button", { name: "编辑" }));
    await user.clear(await screen.findByLabelText("名称"));
    await user.type(screen.getByLabelText("名称"), "已更新的个人分镜提示词");
    await user.click(screen.getByRole("button", { name: "保存并回读" }));
    await waitFor(() => expect(service.updatePrompt).toHaveBeenCalledTimes(1));
    expect(vi.mocked(service.updatePrompt).mock.calls[0]?.[1]).toEqual({ name: "已更新的个人分镜提示词" });
    expect(await screen.findByText(/配置已回读确认/)).toBeInTheDocument();

    const updatedCard = screen.getByText("已更新的个人分镜提示词").closest("article");
    expect(updatedCard).not.toBeNull();
    await user.click(within(updatedCard!).getByRole("button", { name: "删除" }));
    await user.click(within(screen.getByRole("group", { name: "确认删除已更新的个人分镜提示词" }))
      .getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(service.deletePrompt).toHaveBeenCalledWith("owner-storyboard", expect.any(AbortSignal)));
    expect(await screen.findByText("删除结果已回读确认。")).toBeInTheDocument();
    expect(screen.queryByText("已更新的个人分镜提示词")).not.toBeInTheDocument();
  });

  it("preserves empty model identifiers and base URLs unless the user edits them", async () => {
    const user = userEvent.setup();
    const fixture = makeService();
    fixture.addModel(model("owner-empty-fields", {
      name: "空字段模型",
      model: "",
      base_url: "",
      sort_order: 3,
    }));
    fixture.addModel(model("owner-null-fields", {
      name: "空值字段模型",
      model: null,
      base_url: null,
      sort_order: 4,
    }));
    renderPage(fixture.service);

    const card = (await screen.findByText("空字段模型")).closest("article");
    expect(card).not.toBeNull();
    await user.click(within(card!).getByRole("button", { name: "编辑" }));
    await user.click(await screen.findByRole("button", { name: "保存并回读" }));
    expect(fixture.service.updateModel).not.toHaveBeenCalled();

    await user.click(within(card!).getByRole("button", { name: "编辑" }));
    await user.clear(await screen.findByLabelText("名称"));
    await user.type(screen.getByLabelText("名称"), "仅改名称");
    await user.click(screen.getByRole("button", { name: "保存并回读" }));
    await waitFor(() => expect(fixture.service.updateModel).toHaveBeenCalledTimes(1));
    expect(vi.mocked(fixture.service.updateModel).mock.calls[0]?.[1]).toEqual({ name: "仅改名称" });
    expect(await screen.findByText(/配置已回读确认/)).toBeInTheDocument();
    expect(fixture.modelRows().find((item) => item.id === "owner-empty-fields")).toMatchObject({ model: "", base_url: "" });

    const nullCard = screen.getByText("空值字段模型").closest("article");
    expect(nullCard).not.toBeNull();
    await user.click(within(nullCard!).getByRole("button", { name: "编辑" }));
    await user.click(await screen.findByRole("button", { name: "保存并回读" }));
    expect(fixture.service.updateModel).toHaveBeenCalledTimes(1);
    await user.click(within(nullCard!).getByRole("button", { name: "编辑" }));
    await user.clear(await screen.findByLabelText("名称"));
    await user.type(screen.getByLabelText("名称"), "名称变更保留空值");
    await user.click(screen.getByRole("button", { name: "保存并回读" }));
    await waitFor(() => expect(fixture.service.updateModel).toHaveBeenCalledTimes(2));
    expect(vi.mocked(fixture.service.updateModel).mock.calls[1]?.[1]).toEqual({ name: "名称变更保留空值" });
    expect(await screen.findByText(/配置已回读确认/)).toBeInTheDocument();
    expect(fixture.modelRows().find((item) => item.id === "owner-null-fields")).toMatchObject({ model: null, base_url: null });
  });

  it("confirms an OpenAI image model after the backend fills default parameters", async () => {
    const user = userEvent.setup();
    const fixture = makeService();
    fixture.addModel(model("openai-image-empty", {
      category: "image",
      name: "历史 OpenAI 图片模型",
      provider: "openai",
      model: "gpt-image-1",
      params: null,
      sort_order: 0,
    }));
    renderPage(fixture.service);

    await user.selectOptions(screen.getByLabelText("模型分类"), "image");
    const historicalCard = (await screen.findByText("历史 OpenAI 图片模型")).closest("article");
    expect(historicalCard).not.toBeNull();
    await user.click(within(historicalCard!).getByRole("button", { name: "编辑" }));
    await user.clear(await screen.findByLabelText("名称"));
    await user.type(screen.getByLabelText("名称"), "更新历史 OpenAI 图片模型");
    await user.click(screen.getByRole("button", { name: "保存并回读" }));
    await waitFor(() => expect(fixture.service.updateModel).toHaveBeenCalledTimes(1));
    expect(vi.mocked(fixture.service.updateModel).mock.calls[0]?.[1]).toEqual({ name: "更新历史 OpenAI 图片模型" });
    const updated = fixture.modelRows().find((item) => item.id === "openai-image-empty");
    expect(updated?.params).toBe(openAiImageDefaults);
    expect(await screen.findByText(/配置已回读确认/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "新建模型" }));
    await user.type(await screen.findByLabelText("名称"), "OpenAI 图片模型");
    await user.type(screen.getByLabelText("供应商"), "openai");
    await user.type(screen.getByLabelText("API 密钥"), "new-image-key");
    await user.click(screen.getByRole("button", { name: "保存并回读" }));

    await waitFor(() => expect(fixture.service.createModel).toHaveBeenCalledTimes(1));
    expect(vi.mocked(fixture.service.createModel).mock.calls[0]?.[0]).toMatchObject({
      category: "image",
      provider: "openai",
      params: null,
    });
    const saved = fixture.modelRows().find((item) => item.name === "OpenAI 图片模型");
    expect(saved?.params).not.toBeNull();
    expect(JSON.parse(saved?.params ?? "null")).toEqual({
      image_size: { enabled: true, default: "1024x1024", options: ["1024x1024", "1792x1024", "1024x1792"] },
      max_reference_images: { enabled: true, default: 8 },
    });
    expect(await screen.findByText(/配置已回读确认/)).toBeInTheDocument();
  });

  it("preserves nullable prompt fields on a name-only update", async () => {
    const user = userEvent.setup();
    const fixture = makeService();
    fixture.addPrompt(prompt("owner-null-prompt", {
      name: "空内容提示词",
      system_prompt: null,
      user_prompt: null,
      sort_order: 3,
    }));
    renderPage(fixture.service);
    await user.click(screen.getByRole("tab", { name: "提示词" }));

    const card = (await screen.findByText("空内容提示词")).closest("article");
    expect(card).not.toBeNull();
    await user.click(within(card!).getByRole("button", { name: "编辑" }));
    await user.clear(await screen.findByLabelText("名称"));
    await user.type(screen.getByLabelText("名称"), "保留空内容");
    await user.click(screen.getByRole("button", { name: "保存并回读" }));

    await waitFor(() => expect(fixture.service.updatePrompt).toHaveBeenCalledTimes(1));
    expect(vi.mocked(fixture.service.updatePrompt).mock.calls[0]?.[1]).toEqual({ name: "保留空内容" });
    expect(await screen.findByText(/配置已回读确认/)).toBeInTheDocument();
    expect(fixture.promptRows().find((item) => item.id === "owner-null-prompt")).toMatchObject({
      system_prompt: null,
      user_prompt: null,
    });
  });

  it("keeps a key-only network failure unknown after metadata readback", async () => {
    const user = userEvent.setup();
    const fixture = makeService();
    vi.mocked(fixture.service.updateModel).mockRejectedValueOnce(new ApiError("network", "fixture network failure"));
    renderPage(fixture.service);
    const card = (await screen.findByText("我的文本模型")).closest("article");
    expect(card).not.toBeNull();
    await user.click(within(card!).getByRole("button", { name: "编辑" }));
    await user.type(await screen.findByLabelText("API 密钥"), "replacement-secret");
    await user.click(screen.getByRole("button", { name: "保存并回读" }));

    expect(await screen.findByText(/密钥变更结果无法通过只读信息确认/)).toBeInTheDocument();
    expect(fixture.service.updateModel).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("API 密钥")).toHaveValue("replacement-secret");
    expect(screen.getByRole("button", { name: "刷新核实" })).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("owner-secret-never-render");
    expect(document.body).not.toHaveTextContent("replacement-secret");
  });

  it("keeps system model order authoritative without changing system sort fields", async () => {
    const user = userEvent.setup();
    const fixture = makeService();
    fixture.addModel(model("system-text-secondary", {
      name: "第二个系统文本模型",
      is_system: true,
      user_id: "system",
      sort_order: 4,
    }));
    const { service } = fixture;
    renderPage(service);
    const secondSystemCard = (await screen.findByText("第二个系统文本模型")).closest("article");
    expect(secondSystemCard).not.toBeNull();
    await user.click(within(secondSystemCard!).getByRole("button", { name: "将第二个系统文本模型设为默认" }));
    const saveOrder = screen.getByRole("button", { name: "保存排序" });
    await user.click(saveOrder);
    await waitFor(() => expect(service.reorderModels).toHaveBeenCalledTimes(1));
    expect(vi.mocked(service.reorderModels).mock.calls[0]?.[0]).toEqual({
      category: "text",
      model_ids: ["system-text-secondary", "system-text", "owner-text"],
    });
    expect(await screen.findByText("分类顺序已回读确认，第一项现在是此分类默认项。")).toBeInTheDocument();
    expect(fixture.modelRows().filter((item) => item.is_system).map((item) => [item.id, item.sort_order])).toEqual([
      ["system-text", 0],
      ["system-text-secondary", 4],
    ]);
    expect(within(screen.getByLabelText("模型列表")).getAllByRole("article")[0]).toHaveTextContent("第二个系统文本模型");
    cleanup();
    renderPage(service);
    await screen.findByText("第二个系统文本模型");
    expect(within(screen.getByLabelText("模型列表")).getAllByRole("article")[0]).toHaveTextContent("默认");
  });

  it("keeps system prompt order authoritative without changing system sort fields", async () => {
    const user = userEvent.setup();
    const fixture = makeService();
    fixture.addPrompt(prompt("system-storyboard-secondary", {
      name: "第二个系统分镜提示词",
      is_system: true,
      user_id: "system",
      sort_order: 4,
    }));
    renderPage(fixture.service);
    await user.click(screen.getByRole("tab", { name: "提示词" }));
    const secondSystemCard = (await screen.findByText("第二个系统分镜提示词")).closest("article");
    expect(secondSystemCard).not.toBeNull();
    await user.click(within(secondSystemCard!).getByRole("button", { name: "将第二个系统分镜提示词设为默认" }));
    await user.click(screen.getByRole("button", { name: "保存排序" }));

    await waitFor(() => expect(fixture.service.reorderPrompts).toHaveBeenCalledTimes(1));
    expect(vi.mocked(fixture.service.reorderPrompts).mock.calls[0]?.[0]).toEqual({
      category: "storyboard",
      prompt_ids: ["system-storyboard-secondary", "system-storyboard", "owner-storyboard"],
    });
    expect(fixture.promptRows().filter((item) => item.is_system === true && item.category === "storyboard").map((item) => [item.id, item.sort_order])).toEqual([
      ["system-storyboard", 0],
      ["system-storyboard-secondary", 4],
    ]);
    expect(within(screen.getByLabelText("提示词列表")).getAllByRole("article")[0]).toHaveTextContent("第二个系统分镜提示词");
    cleanup();
    renderPage(fixture.service);
    await user.click(await screen.findByRole("tab", { name: "提示词" }));
    await screen.findByText("第二个系统分镜提示词");
    expect(within(screen.getByLabelText("提示词列表")).getAllByRole("article")[0]).toHaveTextContent("默认");
  });

  it("does not submit an ordering draft after it returns to the baseline", async () => {
    const user = userEvent.setup();
    const fixture = makeService();
    renderPage(fixture.service);
    const personalCard = (await screen.findByText("我的文本模型")).closest("article");
    await user.click(within(personalCard!).getByRole("button", { name: "将我的文本模型设为默认" }));
    const systemCard = screen.getByText("系统文本模型").closest("article");
    await user.click(within(systemCard!).getByRole("button", { name: "将系统文本模型设为默认" }));

    expect(screen.getByRole("button", { name: "保存排序" })).toBeDisabled();
    expect(fixture.service.reorderModels).not.toHaveBeenCalled();
  });

  it("does not submit an ordering draft when the category sequence changed", async () => {
    const user = userEvent.setup();
    const { service, addModel } = makeService();
    renderPage(service);
    const personalCard = (await screen.findByText("我的文本模型")).closest("article");
    await user.click(within(personalCard!).getByRole("button", { name: "将我的文本模型设为默认" }));
    addModel(model("new-on-server", { name: "另一窗口新增", sort_order: 2 }));
    await user.click(screen.getByRole("button", { name: "保存排序" }));
    expect(service.reorderModels).not.toHaveBeenCalled();
    expect(await screen.findByText("此分类的配置或顺序已变化；保留当前排序草稿，请基于刷新后的列表重新排序。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存排序" })).toBeDisabled();
    expect(screen.getByText("另一窗口新增")).toBeInTheDocument();
  });

  it("never resubmits an unknown prompt creation after a read-only check", async () => {
    const user = userEvent.setup();
    const { service } = makeService();
    vi.mocked(service.createPrompt).mockRejectedValueOnce(new ApiError("network", "fixture network failure"));
    renderPage(service);
    await user.click(screen.getByRole("tab", { name: "提示词" }));
    await screen.findByText("系统分镜提示词");
    await user.click(screen.getByRole("button", { name: "新建提示词" }));
    await user.type(await screen.findByLabelText("名称"), "未知结果的草稿");
    await user.click(screen.getByRole("button", { name: "保存并回读" }));
    expect(service.createPrompt).toHaveBeenCalledTimes(1);
    await screen.findByText(/已刷新列表，但创建结果仍无法确认/);
    await user.click(screen.getByRole("button", { name: "刷新核实" }));
    await screen.findByText(/创建结果仍无法确认/);
    expect(service.createPrompt).toHaveBeenCalledTimes(1);
  });

  it("ignores an old tab's late 401 after the user changes tabs", async () => {
    const user = userEvent.setup();
    const { service } = makeService();
    const pendingModels = deferred<CreatorModel[]>();
    vi.mocked(service.listModels).mockImplementationOnce(() => pendingModels.promise);
    const { onUnauthorized } = renderPage(service);

    await user.click(screen.getByRole("tab", { name: "提示词" }));
    expect(await screen.findByText("系统分镜提示词")).toBeInTheDocument();
    await act(async () => {
      pendingModels.reject(new ApiError("http", "expired", 401));
      await pendingModels.promise.catch(() => undefined);
    });

    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(screen.getByRole("tab", { name: "提示词" })).toHaveAttribute("aria-selected", "true");
  });

  it("reloads the selected category after cancelling a pending initial list request", async () => {
    const user = userEvent.setup();
    const fixture = makeService();
    fixture.addModel(model("owner-image", { category: "image", name: "我的图片模型" }));
    const pendingModels = deferred<CreatorModel[]>();
    vi.mocked(fixture.service.listModels).mockImplementationOnce(() => pendingModels.promise);
    const { onUnauthorized } = renderPage(fixture.service);

    await user.selectOptions(screen.getByLabelText("模型分类"), "image");
    expect(await screen.findByText("我的图片模型")).toBeInTheDocument();
    expect(fixture.service.listModels).toHaveBeenCalledTimes(2);

    await act(async () => {
      pendingModels.reject(new ApiError("http", "expired", 401));
      await pendingModels.promise.catch(() => undefined);
    });

    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(screen.getByLabelText("模型分类")).toHaveValue("image");
    expect(screen.getByText("我的图片模型")).toBeInTheDocument();
  });

  it("locks scope controls and editor fields during a create request", async () => {
    const user = userEvent.setup();
    const { service } = makeService();
    const pendingCreate = deferred<void>();
    const createModel = vi.mocked(service.createModel).getMockImplementation();
    if (createModel === undefined) throw new Error("Expected the model creation fixture to have an implementation.");
    vi.mocked(service.createModel).mockImplementation(async (input, signal) => {
      await pendingCreate.promise;
      return createModel(input, signal);
    });
    renderPage(service);

    await user.click(screen.getByRole("button", { name: "新建模型" }));
    await user.type(await screen.findByLabelText("名称"), "等待中的模型");
    await user.type(screen.getByLabelText("供应商"), "fixture-provider");
    await user.type(screen.getByLabelText("API 密钥"), "fresh-key");
    await user.click(screen.getByRole("button", { name: "保存并回读" }));
    await waitFor(() => expect(service.createModel).toHaveBeenCalledTimes(1));

    const promptTab = screen.getByRole("tab", { name: "提示词" });
    expect(promptTab).toBeDisabled();
    expect(screen.getByLabelText("模型分类")).toBeDisabled();
    expect(screen.getByLabelText("名称")).toBeDisabled();
    await user.click(promptTab);
    expect(promptTab).toHaveAttribute("aria-selected", "false");

    await act(async () => {
      pendingCreate.resolve(undefined);
      await pendingCreate.promise;
    });
    expect(await screen.findByText("等待中的模型")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "模型" })).toHaveAttribute("aria-selected", "true");
  });

  it("clears a pending delete confirmation when changing categories", async () => {
    const user = userEvent.setup();
    const { service } = makeService();
    renderPage(service);
    const personalCard = (await screen.findByText("我的文本模型")).closest("article");
    expect(personalCard).not.toBeNull();
    await user.click(within(personalCard!).getByRole("button", { name: "删除" }));
    expect(screen.getByRole("group", { name: "确认删除我的文本模型" })).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("模型分类"), "image");

    expect(screen.queryByRole("group", { name: "确认删除我的文本模型" })).not.toBeInTheDocument();
    expect(service.deleteModel).not.toHaveBeenCalled();
  });

  it("supports keyboard tab navigation at a narrow viewport", async () => {
    const user = userEvent.setup();
    const widthDescriptor = Object.getOwnPropertyDescriptor(window, "innerWidth");
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    try {
      const { service } = makeService();
      renderPage(service);
      const modelsTab = screen.getByRole("tab", { name: "模型" });
      modelsTab.focus();
      await user.keyboard("{ArrowRight}");
      const promptsTab = screen.getByRole("tab", { name: "提示词" });
      expect(promptsTab).toHaveAttribute("aria-selected", "true");
      expect(promptsTab).toHaveFocus();
      expect(modelsTab).toHaveAttribute("tabindex", "-1");
    } finally {
      if (widthDescriptor === undefined) {
        Reflect.deleteProperty(window, "innerWidth");
      } else {
        Object.defineProperty(window, "innerWidth", widthDescriptor);
      }
    }
  });

  it("tests the explicitly entered model key without saving and sanitizes success and failure messages", async () => {
    const user = userEvent.setup();
    const { service } = makeService();
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "connection-test-session");
    const requests: Array<{ url: string; method: string; authorization: string | null; body: unknown }> = [];
    const connectionResults: CreatorModelConnectionTestResult[] = [
      {
        success: true,
        message: "连接成功，回显 explicit-test-key",
        status_code: 200,
      },
      {
        success: false,
        message: "连接失败，拒绝 explicit-test-key",
        status_code: 401,
      },
    ];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      const rawBody = typeof init?.body === "string" ? init.body : "";
      requests.push({
        url: url.toString(),
        method: init?.method ?? "",
        authorization: new Headers(init?.headers).get("Authorization"),
        body: rawBody === "" ? null : JSON.parse(rawBody) as unknown,
      });
      const result = connectionResults.shift();
      if (result === undefined) throw new Error("unexpected connection test request");
      return new Response(JSON.stringify(result), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as typeof fetch;
    const apiServices = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const apiCreatorSettings = apiServices.creatorSettings;
    const apiTestModelConnection = apiCreatorSettings?.testModelConnection;
    if (apiTestModelConnection === undefined) {
      throw new Error("API creator settings connection test port is unavailable");
    }
    service.testModelConnection = apiTestModelConnection;
    const { onUnauthorized } = renderPage(service);

    const modelCard = (await screen.findByText("我的文本模型")).closest("article");
    expect(modelCard).not.toBeNull();
    await user.click(within(modelCard!).getByRole("button", { name: "编辑" }));
    expect(await screen.findByLabelText("API 密钥")).toHaveValue("");
    expect(screen.getByRole("button", { name: "测试连接" })).toBeDisabled();
    expect(screen.getByText(/编辑已保存模型时，请重新输入密钥后再测试/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "放弃草稿" }));

    await user.click(screen.getByRole("button", { name: "新建模型" }));
    await user.type(screen.getByLabelText("名称"), "连接测试草稿");
    await user.type(screen.getByLabelText("供应商"), "openai");
    await user.type(screen.getByLabelText("模型标识"), "model-a");
    await user.type(screen.getByLabelText("服务地址"), "https://models.example.test/v1");
    await user.type(screen.getByLabelText("API 密钥"), "explicit-test-key");

    const testButton = screen.getByRole("button", { name: "测试连接" });
    await user.click(testButton);
    expect(await screen.findByRole("status")).toHaveTextContent("连接成功，回显 [密钥已隐藏]");
    expect(requests).toHaveLength(1);
    expect(requests[0]).toEqual({
      url: "http://127.0.0.1:4175/api/models/test-connection",
      method: "POST",
      authorization: "Bearer connection-test-session",
      body: {
        provider: "openai",
        api_key: "explicit-test-key",
        base_url: "https://models.example.test/v1",
        model: "model-a",
        category: "text",
      },
    });
    expect(service.createModel).not.toHaveBeenCalled();
    expect(service.updateModel).not.toHaveBeenCalled();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(document.body).not.toHaveTextContent("explicit-test-key");

    await user.click(testButton);
    expect(await screen.findByRole("alert")).toHaveTextContent("连接失败，拒绝 [密钥已隐藏]");
    expect(requests).toHaveLength(2);
    expect(requests[1]?.body).toEqual(requests[0]?.body);
    expect(service.createModel).not.toHaveBeenCalled();
    expect(service.updateModel).not.toHaveBeenCalled();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(document.body).not.toHaveTextContent("explicit-test-key");
  });

  it("ignores late connection-test results after draft changes and never retries automatically", async () => {
    const user = userEvent.setup();
    const { service } = makeService();
    const lateResult = deferred<CreatorModelConnectionTestResult>();
    const rejectedResult = deferred<CreatorModelConnectionTestResult>();
    service.testModelConnection = vi.fn()
      .mockImplementationOnce(() => lateResult.promise)
      .mockImplementationOnce(() => rejectedResult.promise);
    const { onUnauthorized } = renderPage(service);

    await user.click(await screen.findByRole("button", { name: "新建模型" }));
    await user.type(screen.getByLabelText("名称"), "迟到回执草稿");
    await user.type(screen.getByLabelText("供应商"), "openai");
    await user.type(screen.getByLabelText("模型标识"), "model-before-edit");
    await user.type(screen.getByLabelText("API 密钥"), "late-test-key");
    await user.click(screen.getByRole("button", { name: "测试连接" }));
    expect(service.testModelConnection).toHaveBeenCalledTimes(1);

    await user.clear(screen.getByLabelText("模型标识"));
    await user.type(screen.getByLabelText("模型标识"), "model-after-edit");
    await act(async () => {
      lateResult.resolve({ success: false, message: "迟到错误中包含 late-test-key" });
    });
    expect(screen.queryByText(/迟到错误/)).not.toBeInTheDocument();
    expect(service.testModelConnection).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "测试连接" }));
    expect(service.testModelConnection).toHaveBeenCalledTimes(2);
    await act(async () => {
      rejectedResult.reject(new Error("provider echoed late-test-key"));
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("provider echoed [密钥已隐藏]");
    expect(document.body).not.toHaveTextContent("late-test-key");
    expect(service.testModelConnection).toHaveBeenCalledTimes(2);
    expect(service.createModel).not.toHaveBeenCalled();
    expect(service.updateModel).not.toHaveBeenCalled();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("converts personal and system models through their new IDs and verifies the empty-body API routes", async () => {
    const user = userEvent.setup();
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "model-conversion-session");
    let serverModels: Array<CreatorModel & { api_key: string }> = [
      { ...model("personal-original", { name: "待转换模型", provider: "openai" }), api_key: "conversion-secret" },
    ];
    const requests: Array<{ path: string; method: string; authorization: string | null; body: BodyInit | null }> = [];
    const respond = (status: number, value: unknown) => new Response(JSON.stringify(value), {
      status,
      headers: { "Content-Type": "application/json" },
    });
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      requests.push({
        path: url.pathname,
        method: init?.method ?? "GET",
        authorization: new Headers(init?.headers).get("Authorization"),
        body: init?.body ?? null,
      });
      if (init?.method === "GET" && url.pathname === "/api/models") {
        return respond(200, serverModels);
      }
      if (init?.method === "POST" && url.pathname === "/api/models/personal-original/promote-to-system") {
        const source = serverModels.find((item) => item.id === "personal-original");
        if (source === undefined) return respond(404, { detail: "not found" });
        const promoted = { ...source, id: "system-promoted", user_id: "system", is_system: true };
        serverModels = [promoted];
        return respond(200, promoted);
      }
      if (init?.method === "POST" && url.pathname === "/api/models/system-promoted/demote-to-personal") {
        const source = serverModels.find((item) => item.id === "system-promoted");
        if (source === undefined) return respond(404, { detail: "not found" });
        const personal = { ...source, id: "personal-demoted", user_id: ownerId, is_system: false };
        serverModels = [personal];
        return respond(200, personal);
      }
      return respond(404, { detail: "unexpected route" });
    }) as typeof fetch;
    const apiServices = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const service = apiServices.creatorSettings;
    if (service?.promoteModelToSystem === undefined || service.demoteModelToPersonal === undefined) {
      throw new Error("API creator settings model conversion ports are unavailable");
    }
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { onUnauthorized } = renderPage(service, ownerId, true);

    await user.click(await screen.findByRole("button", { name: "设为系统" }));
    expect(await screen.findByText("个人模型已按新 ID 转为系统模型，原记录已移除并回读确认。"))
      .toBeInTheDocument();
    expect(await screen.findByText("系统只读")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "转为个人" }));
    expect(await screen.findByText("系统模型已按新 ID 转为当前个人模型，原记录已移除并回读确认。"))
      .toBeInTheDocument();
    const demotedCard = (await screen.findByText("待转换模型")).closest("article");
    expect(demotedCard).not.toBeNull();
    expect(within(demotedCard!).getByText("个人配置")).toBeInTheDocument();

    const posts = requests.filter((request) => request.method === "POST");
    expect(posts.map(({ path, body }) => ({ path, body }))).toEqual([
      { path: "/api/models/personal-original/promote-to-system", body: null },
      { path: "/api/models/system-promoted/demote-to-personal", body: null },
    ]);
    expect(posts.every((request) => request.authorization === "Bearer model-conversion-session")).toBe(true);
    expect(requests.filter((request) => request.method === "GET" && request.path === "/api/models")).toHaveLength(3);
    expect(confirmSpy).toHaveBeenCalledTimes(2);
    expect(serverModels[0]?.id).toBe("personal-demoted");
    expect(document.body).not.toHaveTextContent("conversion-secret");
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("hides conversion from regular users and keeps an administrator signed in after 403", async () => {
    const user = userEvent.setup();
    const { service } = makeService();
    service.promoteModelToSystem = vi.fn(async () => {
      throw new ApiError("http", "Forbidden", 403);
    });
    const regularUser = renderPage(service, ownerId, false);
    const personalCard = (await screen.findByText("我的文本模型")).closest("article");
    expect(personalCard).not.toBeNull();
    expect(screen.queryByRole("button", { name: "设为系统" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "转为个人" })).not.toBeInTheDocument();
    expect(service.promoteModelToSystem).not.toHaveBeenCalled();

    cleanup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const administrator = renderPage(service, ownerId, true);
    const adminPersonalCard = (await screen.findByText("我的文本模型")).closest("article");
    expect(adminPersonalCard).not.toBeNull();
    await user.click(within(adminPersonalCard!).getByRole("button", { name: "设为系统" }));
    expect(await screen.findByText("服务端拒绝了此转换，当前账号仍保持登录。"))
      .toBeInTheDocument();
    expect(service.promoteModelToSystem).toHaveBeenCalledTimes(1);
    expect(administrator.onUnauthorized).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "停止本地核对" })).not.toBeInTheDocument();
    expect(regularUser.onUnauthorized).not.toHaveBeenCalled();
    expect(confirmSpy).toHaveBeenCalledTimes(1);
  });

  it("treats HTTP 408 as unknown, allows stopping local tracking, and ignores the late old-actor receipt", async () => {
    const user = userEvent.setup();
    const { service } = makeService();
    const lateReceipt = deferred<CreatorModel>();
    service.promoteModelToSystem = vi.fn()
      .mockRejectedValueOnce(new ApiError("http", "Request timeout", 408))
      .mockImplementationOnce(() => lateReceipt.promise);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { onUnauthorized, view } = renderPage(service, ownerId, true);

    await user.click(await screen.findByRole("button", { name: "设为系统" }));
    await user.click(await screen.findByRole("button", { name: "停止本地核对" }));
    expect(await screen.findByText("已停止本地核对；服务端转换可能已生效，请先刷新列表并确认状态。"))
      .toBeInTheDocument();
    expect(service.promoteModelToSystem).toHaveBeenCalledTimes(1);
    expect(service.listModels).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "设为系统" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "设为系统" }));
    expect(service.promoteModelToSystem).toHaveBeenCalledTimes(2);
    view.rerender(
      <CreatorSettingsPage
        isSuperuser={false}
        onBack={vi.fn()}
        onUnauthorized={onUnauthorized}
        service={service}
        userId={otherOwnerId}
      />,
    );
    await screen.findByText("系统文本模型");
    await act(async () => {
      lateReceipt.resolve(model("late-system-model", { is_system: true, user_id: "system" }));
      await lateReceipt.promise;
    });
    expect(screen.queryByText("转换请求已返回，正在只读核对原记录和新记录…")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "设为系统" })).not.toBeInTheDocument();
    expect(service.promoteModelToSystem).toHaveBeenCalledTimes(2);
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(confirmSpy).toHaveBeenCalledTimes(3);
  });

  it("opens quota lookup only for administrators, preserves drafts, isolates cards, and drops a late previous-actor result", async () => {
    const user = userEvent.setup();
    const { service: settingsService } = makeService();
    const accounts: ProviderQuotaAccount[] = [
      { id: "quota-zero", name: "零余额", providerType: "deepseek", isActive: true, sortOrder: 0, consoleUrl: null },
      { id: "quota-error", name: "异常账户", providerType: "new_api", isActive: true, sortOrder: 1, consoleUrl: null },
      { id: "quota-slow", name: "迟到账户", providerType: "new_api", isActive: true, sortOrder: 2, consoleUrl: null },
      { id: "quota-disabled", name: "停用账户", providerType: "aliyun", isActive: false, sortOrder: 3, consoleUrl: null },
    ];
    const lateBalance = deferred<ProviderQuotaBalance>();
    const makeBalance = (
      id: string,
      overrides: Partial<ProviderQuotaBalance> = {},
    ): ProviderQuotaBalance => ({
      id,
      name: `额度 ${id}`,
      providerType: "deepseek",
      isActive: true,
      disabled: false,
      configured: true,
      unit: "$",
      unitSuffix: "",
      decimals: 2,
      balanceLabel: "剩余额度",
      warnBelow: 10,
      balance: 12,
      used: null,
      total: null,
      toppedUp: null,
      granted: null,
      credits: null,
      balanceCny: null,
      available: null,
      extraMeta: [],
      updatedAt: "2026-10-27 10:30:00",
      hasErrors: false,
      sessionReused: false,
      consoleUrl: null,
      ...overrides,
    });
    const quotaService: ProviderQuotaServicePort = {
      listAccounts: vi.fn(async () => accounts),
      getBalance: vi.fn(async (accountId: string) => {
        if (accountId === "quota-zero") return makeBalance(accountId, { balance: 0, total: 0 });
        if (accountId === "quota-error") return makeBalance(accountId, { balance: null, hasErrors: true });
        if (accountId === "quota-slow") return lateBalance.promise;
        throw new Error("inactive account must not be queried");
      }),
    };

    const regularUser = renderPage(settingsService, ownerId, false, quotaService);
    await screen.findByText("我的文本模型");
    expect(screen.queryByRole("button", { name: "供应商额度监控" })).not.toBeInTheDocument();
    expect(quotaService.listAccounts).not.toHaveBeenCalled();
    expect(quotaService.getBalance).not.toHaveBeenCalled();
    cleanup();

    const { onUnauthorized, view } = renderPage(settingsService, ownerId, true, quotaService);
    const personalCard = (await screen.findByText("我的文本模型")).closest("article");
    expect(personalCard).not.toBeNull();
    await user.click(within(personalCard!).getByRole("button", { name: "编辑" }));
    const draftName = await screen.findByLabelText("名称");
    await user.clear(draftName);
    await user.type(draftName, "未保存的模型草稿");

    await user.click(await screen.findByRole("button", { name: "供应商额度监控" }));
    expect(await screen.findByRole("region", { name: "供应商额度监控" })).toBeInTheDocument();
    expect(screen.getByLabelText("名称")).toHaveValue("未保存的模型草稿");
    expect(await screen.findByText("监控账号 4 家")).toBeInTheDocument();
    expect(await screen.findByText("$0.00")).toBeInTheDocument();
    expect(screen.getByText("总额 $0.00")).toBeInTheDocument();
    expect(await screen.findByText("额度查询异常，请检查该账号配置或稍后重试。"))
      .toBeInTheDocument();
    expect(screen.getByText("该监控账号已停用。")).toBeInTheDocument();
    expect(screen.queryByText(/总剩余额度/)).not.toBeInTheDocument();
    expect(quotaService.listAccounts).toHaveBeenCalledTimes(1);
    expect(quotaService.getBalance).toHaveBeenCalledTimes(3);
    expect(quotaService.getBalance).not.toHaveBeenCalledWith("quota-disabled", expect.anything());

    view.rerender(
      <CreatorSettingsPage
        isSuperuser={false}
        onBack={vi.fn()}
        onUnauthorized={onUnauthorized}
        providerQuotaService={quotaService}
        service={settingsService}
        userId={otherOwnerId}
      />,
    );
    await act(async () => {
      lateBalance.resolve(makeBalance("quota-slow", { name: "不可显示的迟到余额" }));
      await lateBalance.promise;
    });
    expect(screen.queryByRole("region", { name: "供应商额度监控" })).not.toBeInTheDocument();
    expect(screen.queryByText("不可显示的迟到余额")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "供应商额度监控" })).not.toBeInTheDocument();
    expect(quotaService.listAccounts).toHaveBeenCalledTimes(1);
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(regularUser.onUnauthorized).not.toHaveBeenCalled();
  });

  it("drops api_key values from model response projections", () => {
    const parsed = parseCreatorModel({
      id: "secret-model",
      category: "unknown-provider-category",
      name: "无密钥字段投影",
      provider: "fixture",
      api_key: "must-not-survive-parser",
      model: "m-1",
      base_url: null,
      params: null,
      sort_order: 0,
      is_system: false,
      user_id: ownerId,
    });
    expect(parsed.category).toBe("unknown-provider-category");
    expect(parsed).not.toHaveProperty("api_key");
    expect(JSON.stringify(parsed)).not.toContain("must-not-survive-parser");
  });
});
