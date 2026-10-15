import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../shared/api/errors";
import type {
  CreatorModel,
  CreatorModelCreateInput,
  CreatorModelUpdateInput,
  CreatorPrompt,
  CreatorPromptCreateInput,
  CreatorPromptUpdateInput,
  CreatorSettingsServicePort,
} from "../../shared/api/creatorSettings";
import { parseCreatorModel } from "../../shared/api/creatorSettings";
import { CreatorSettingsPage } from "./CreatorSettingsPage";

afterEach(cleanup);

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

function renderPage(service: CreatorSettingsServicePort, userId = ownerId) {
  const onUnauthorized = vi.fn();
  const onBack = vi.fn();
  render(<CreatorSettingsPage onBack={onBack} onUnauthorized={onUnauthorized} service={service} userId={userId} />);
  return { onUnauthorized, onBack };
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
