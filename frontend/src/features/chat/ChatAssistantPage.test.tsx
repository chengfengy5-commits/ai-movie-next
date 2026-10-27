import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter, Character, Prop, Scene, Series, StoryboardAsset } from "../../shared/api/contracts";
import type { CreatorPrompt } from "../../shared/api/creatorSettings";
import type { ChatAssistantMessage } from "../../shared/api/chatAssistant";
import { createApiServices, type WorkspaceServices } from "../../shared/api/services";
import type { StoryboardModelChoice } from "../storyboard-generation/contracts";
import { ChatAssistantPage, type ChatAssistantSourceIntent } from "./ChatAssistantPage";
import {
  chatCategoryPreferenceKey,
  readChatCategoryPreference,
  writeChatCategoryPreference,
} from "./chatCategoryPreferences";
import { writeChatContextPreference } from "./chatContextPreferences";

const originalClipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");

const chapter: Chapter = {
  id: "chapter-952",
  series_id: "series-952",
  title: "第 1 集",
  content: [{ description: "开场镜头" }],
  order: 1,
  created_at: "2026-10-26T00:00:00Z",
  updated_at: "2026-10-26T00:00:00Z",
  lock: null,
};

const alternateChapter: Chapter = {
  ...chapter,
  id: "chapter-952-alternate",
  title: "第 2 集",
  order: 2,
};

const chapterWithStableFrames: Chapter = {
  ...chapter,
  content: [
    { description: "第一镜", storyboard: ["board-952-first"] },
    { description: "第二镜", storyboard: ["board-952-target"] },
  ],
};

function storyboardAsset(id: string, frameIndex: number, chapterId = chapter.id): StoryboardAsset {
  return {
    id,
    series_id: series.id,
    chapter_id: chapterId,
    frame_index: frameIndex,
    name: id,
    description: null,
    image_url: null,
    created_at: "2026-10-26T00:00:00Z",
    updated_at: "2026-10-26T00:00:00Z",
  };
}

const series: Series = {
  id: "series-952",
  user_id: "actor-952",
  name: "测试剧集",
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
  created_at: "2026-10-26T00:00:00Z",
  updated_at: "2026-10-26T00:00:00Z",
};

const model: StoryboardModelChoice = {
  id: "model-952",
  name: "测试文本模型",
  category: "text",
  provider: "fixture",
  is_system: true,
  cost_per_call: 2,
};

const reasoningModel: StoryboardModelChoice = {
  ...model,
  id: "model-952-reasoning",
  name: "测试推理模型",
  category: "reasoning",
};

const prompt: CreatorPrompt = {
  id: "prompt-952",
  category: "chat-assistant",
  name: "人物顾问",
  system_prompt: "只依据已知剧情回答。",
  user_prompt: "请用简洁中文回答。",
  sort_order: 1,
  is_system: true,
  user_id: "actor-952",
};

const alternatePrompt: CreatorPrompt = {
  ...prompt,
  id: "prompt-952-alternate",
  name: "推理顾问",
  system_prompt: "先梳理事实，再回答。",
  user_prompt: "请给出分步说明。",
};

interface TaskReceiptFixture {
  readonly id: string;
  readonly type: "chat";
  readonly message_id: string;
  readonly status: string;
  readonly result: string;
  readonly billing_status: string;
  readonly quoted_amount: number;
  readonly progress: number;
  readonly progress_message: string | null;
  readonly external_task_id: string | null;
  readonly external_provider: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

type StreamMode = "complete" | "disconnect";

function taskReceipt(messageId: string, result: string, status = "completed"): TaskReceiptFixture {
  return {
    id: `task-${messageId}`,
    type: "chat",
    message_id: messageId,
    status,
    result,
    billing_status: "charged",
    quoted_amount: 2,
    progress: status === "completed" || status === "failed" ? 100 : 50,
    progress_message: null,
    external_task_id: null,
    external_provider: null,
    created_at: "2026-10-26T00:00:01Z",
    updated_at: "2026-10-26T00:00:02Z",
  };
}

function message(input: Partial<ChatAssistantMessage> & Pick<ChatAssistantMessage, "id" | "role" | "content">): ChatAssistantMessage {
  return {
    id: input.id,
    chapterId: input.chapterId ?? chapter.id,
    frameIndex: input.frameIndex ?? null,
    assetType: input.assetType ?? null,
    assetId: input.assetId ?? null,
    chatMode: "chat",
    role: input.role,
    content: input.content,
    modelName: input.modelName ?? null,
    createdAt: input.createdAt ?? "2026-10-26T00:00:00Z",
  };
}

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

import { API_TOKEN_KEY, API_USER_KEY, type StorageLike } from "../../shared/api/storage";

function setupFixture(options: {
  readonly initialMessages?: readonly ChatAssistantMessage[];
  readonly initialTasks?: readonly TaskReceiptFixture[];
  readonly streamMode?: StreamMode;
  readonly assistantCreateResponseLost?: boolean;
  readonly models?: readonly StoryboardModelChoice[];
  readonly prompts?: readonly CreatorPrompt[];
  readonly chapters?: readonly Chapter[];
  readonly storyboards?: readonly StoryboardAsset[];
  readonly characters?: readonly Character[];
  readonly scenes?: readonly Scene[];
  readonly props?: readonly Prop[];
} = {}) {
  let messages = [...(options.initialMessages ?? [])];
  const tasksByMessage = new Map((options.initialTasks ?? []).map((task) => [task.message_id, task]));
  const calls: Array<{ url: URL; method: string; body: unknown; headers: Headers }> = [];
  let nextMessage = 1;
  let assistantCreateResponseLost = false;
  let streamBody: Record<string, unknown> | null = null;
  const storage = new MemoryStorage();
  storage.setItem(API_TOKEN_KEY, "chat-token-952");
  storage.setItem(API_USER_KEY, JSON.stringify({ id: "actor-952" }));

  const fetcher = vi.fn<typeof fetch>(async (input, init) => {
    const url = new URL(String(input));
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) as unknown : null;
    calls.push({ url, method, body, headers: new Headers(init?.headers) });

    if (url.pathname === "/api/chat/stream" && method === "POST") {
      streamBody = body as Record<string, unknown>;
      const assistantId = String(streamBody.message_id);
      const answer = options.streamMode === "disconnect" ? "断流后仍完成" : "助手回答：你好";
      messages = messages.map((row) => row.id === assistantId ? { ...row, content: answer } : row);
      tasksByMessage.set(assistantId, taskReceipt(assistantId, answer));
      if (options.streamMode === "disconnect") {
        const responseBody = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(
              'data: {"type":"start"}\n\ndata: {"type":"content","text":"断流","full_text":"断流"}\n\n',
            ));
            controller.error(new Error("fixture stream disconnected"));
          },
        });
        return new Response(responseBody, { status: 200, headers: { "Content-Type": "text/event-stream" } });
      }
      const events = [
        { type: "start" },
        { type: "content", text: "助手回答：你好", full_text: "助手回答：你好" },
        { type: "complete", raw_response: "助手回答：你好" },
        { type: "done" },
      ];
      const responseBody = new ReadableStream<Uint8Array>({
        start(controller) {
          for (const event of events) controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`));
          controller.close();
        },
      });
      return new Response(responseBody, { status: 200, headers: { "Content-Type": "text/event-stream; charset=utf-8" } });
    }

    if (url.pathname.endsWith("/chat-messages") && method === "GET") {
      const selectedFrame = url.searchParams.get("frame_index");
      const selected = selectedFrame === null
        ? messages.filter((row) => row.frameIndex === null && row.assetType === null)
        : messages.filter((row) => row.frameIndex === Number(selectedFrame) && row.assetType === null);
      return Response.json(selected.map(toWireMessage));
    }

    if (url.pathname.endsWith("/chat-messages") && method === "POST") {
      const input = body as Record<string, unknown>;
      const created = message({
        id: `server-message-${nextMessage++}`,
        chapterId: String(input.chapter_id),
        frameIndex: typeof input.frame_index === "number" ? input.frame_index : null,
        assetType: typeof input.asset_type === "string" ? input.asset_type as ChatAssistantMessage["assetType"] : null,
        assetId: typeof input.asset_id === "string" ? input.asset_id : null,
        role: input.role as "user" | "assistant",
        content: String(input.content),
        modelName: typeof input.model_name === "string" ? input.model_name : null,
        createdAt: new Date(Date.now() + nextMessage * 1_000).toISOString(),
      });
      messages = [...messages, created];
      if (input.role === "assistant" && options.assistantCreateResponseLost && !assistantCreateResponseLost) {
        assistantCreateResponseLost = true;
        throw new TypeError("fixture lost assistant creation response");
      }
      return Response.json(toWireMessage(created), { status: 201 });
    }

    if (url.pathname === "/api/chat/tasks" && method === "GET") {
      const messageId = url.searchParams.get("message_id");
      if (messageId !== null) return Response.json(tasksByMessage.get(messageId) ?? { status: null, result: null });
      const taskId = url.searchParams.get("task_id");
      const receipt = [...tasksByMessage.values()].find((task) => task.id === taskId);
      return receipt === undefined ? Response.json({ detail: "task missing" }, { status: 404 }) : Response.json(receipt);
    }

    if (url.pathname.includes("/chat-messages/single/") && method === "DELETE") {
      const id = decodeURIComponent(url.pathname.split("/").at(-1) ?? "");
      messages = messages.filter((row) => row.id !== id);
      return new Response(null, { status: 204 });
    }

    return Response.json({ detail: `unexpected fixture route ${method} ${url.pathname}` }, { status: 500 });
  });

  const api = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
  const services: WorkspaceServices = {
    ...api,
    listChapters: vi.fn(async () => [...(options.chapters ?? [chapter])]),
    listStoryboardAssets: vi.fn(async () => [...(options.storyboards ?? [])]),
    listCharacters: vi.fn(async () => [...(options.characters ?? [])]),
    listScenes: vi.fn(async () => [...(options.scenes ?? [])]),
    listProps: vi.fn(async () => [...(options.props ?? [])]),
    storyboardGeneration: {
      ...api.storyboardGeneration!,
      listModels: vi.fn(async () => [...(options.models ?? [model])]),
    },
    creatorSettings: {
      ...api.creatorSettings!,
      listPrompts: vi.fn(async (category) => category === "chat-assistant" ? [...(options.prompts ?? [prompt])] : []),
    },
  };
  return { services, calls, fetcher, storage, get messages() { return messages; }, get streamBody() { return streamBody; } };
}

function toWireMessage(row: ChatAssistantMessage): Record<string, unknown> {
  return {
    id: row.id,
    chapter_id: row.chapterId,
    frame_index: row.frameIndex,
    asset_type: row.assetType,
    asset_id: row.assetId,
    chat_mode: row.chatMode,
    role: row.role,
    content: row.content,
    model_name: row.modelName,
    created_at: row.createdAt,
  };
}

function renderPage(
  services: WorkspaceServices,
  onNavigateSource = vi.fn<(target: ChatAssistantSourceIntent) => void>(),
  identity: { readonly userId?: string; readonly sessionGeneration?: number; readonly series?: Series } = {},
) {
  const onUnauthorized = vi.fn();
  const onBack = vi.fn();
  const renderWithServices = (
    nextServices: WorkspaceServices,
    nextIdentity = identity,
  ) => (
    <ChatAssistantPage
      services={nextServices}
      series={nextIdentity.series ?? series}
      userId={nextIdentity.userId ?? "actor-952"}
      sessionGeneration={nextIdentity.sessionGeneration ?? 1}
      onUnauthorized={onUnauthorized}
      onBack={onBack}
      onNavigateSource={onNavigateSource}
    />
  );
  const rendered = render(renderWithServices(services));
  return {
    ...rendered,
    onUnauthorized,
    onBack,
    onNavigateSource,
    rerenderWithServices(
      nextServices: WorkspaceServices,
      nextIdentity = identity,
    ) {
      rendered.rerender(renderWithServices(nextServices, nextIdentity));
    },
  };
}

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
  window.localStorage.clear();
  if (originalClipboardDescriptor === undefined) Reflect.deleteProperty(navigator, "clipboard");
  else Object.defineProperty(navigator, "clipboard", originalClipboardDescriptor);
  vi.restoreAllMocks();
});

describe("ChatAssistantPage", () => {
  it("isolates saved selections by account, API, and chat category", () => {
    const storage = new MemoryStorage();
    const scope = { apiBaseUrl: "https://api.example.test/api", userId: "actor-pref" };
    const preference = { modelId: model.id, promptId: prompt.id };

    expect(writeChatCategoryPreference(scope, "general", preference, storage)).toBe(true);
    expect(readChatCategoryPreference(scope, "general", storage)).toEqual(preference);
    expect(readChatCategoryPreference(scope, "frame", storage)).toBeNull();
    expect(readChatCategoryPreference({ ...scope, userId: "other-actor" }, "general", storage)).toBeNull();
    expect(readChatCategoryPreference({ ...scope, apiBaseUrl: "https://other.example.test/api" }, "general", storage)).toBeNull();
    expect(chatCategoryPreferenceKey(scope, "general")).not.toBe(chatCategoryPreferenceKey(scope, "frame"));

    const unavailable = {
      getItem: () => { throw new Error("storage unavailable"); },
      setItem: () => { throw new Error("storage unavailable"); },
    };
    expect(readChatCategoryPreference(scope, "general", unavailable)).toBeNull();
    expect(writeChatCategoryPreference(scope, "general", preference, unavailable)).toBe(false);
  });

  it("restores a stable storyboard target into the history and chat stream after frame order changes", async () => {
    const user = userEvent.setup();
    const historicalMessage = message({
      id: "stable-frame-history-952",
      chapterId: chapter.id,
      frameIndex: 1,
      role: "user",
      content: "目标镜头的历史消息",
    });
    const fixture = setupFixture({
      chapters: [chapterWithStableFrames],
      storyboards: [storyboardAsset("board-952-first", 0), storyboardAsset("board-952-target", 1)],
      initialMessages: [historicalMessage],
    });
    expect(writeChatContextPreference({
      apiBaseUrl: fixture.services.apiBaseUrl,
      userId: "actor-952",
      seriesId: series.id,
    }, { kind: "frame", chapterId: chapter.id, storyboardAssetId: "board-952-target" })).toBe("saved");

    renderPage(fixture.services);

    await screen.findByText("目标镜头的历史消息");
    expect(screen.getByLabelText("会话目标")).toHaveValue("frame:1");
    const historyRequest = fixture.calls.find((call) => call.method === "GET" && call.url.pathname.endsWith("/chat-messages"));
    expect(historyRequest?.url.searchParams.get("frame_index")).toBe("1");

    await user.selectOptions(await screen.findByLabelText("文本模型"), model.id);
    await user.type(screen.getByLabelText("发送给 AI 助手"), "继续目标镜头");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await screen.findByText("聊天已完成，并已核对原任务回执与服务器消息。");
    const framePosts = fixture.calls.filter((call) => call.method === "POST" && call.url.pathname.endsWith("/chat-messages"));
    expect(framePosts.map((call) => (call.body as Record<string, unknown>).frame_index)).toEqual([1, 1]);
    expect(fixture.streamBody?.messages).toMatchObject([
      { role: "user", content: "继续目标镜头" },
    ]);
  });

  it("does not restore another actor or API scope", async () => {
    const fixture = setupFixture({ chapters: [chapter, alternateChapter] });
    expect(writeChatContextPreference({
      apiBaseUrl: fixture.services.apiBaseUrl,
      userId: "actor-952",
      seriesId: series.id,
    }, { kind: "chapter", chapterId: alternateChapter.id })).toBe("saved");

    const actorScoped = renderPage(fixture.services, undefined, { userId: "actor-other" });
    await waitFor(() => expect(screen.getByLabelText("章节")).toHaveValue(chapter.id));
    expect(screen.getByLabelText("会话目标")).toHaveValue("chapter");
    actorScoped.rerenderWithServices({ ...fixture.services, apiBaseUrl: "https://other.example.test/api" });
    await waitFor(() => expect(screen.getByLabelText("章节")).toHaveValue(chapter.id));
    expect(screen.getByLabelText("会话目标")).toHaveValue("chapter");
    actorScoped.rerenderWithServices(
      { ...fixture.services, apiBaseUrl: "https://other.example.test/api" },
      { userId: "actor-952" },
    );
    await waitFor(() => expect(screen.getByLabelText("章节")).toHaveValue(chapter.id));
    expect(screen.getByLabelText("会话目标")).toHaveValue("chapter");
  });

  it("falls back safely when a saved storyboard ID no longer resolves in current chapter content", async () => {
    const fixture = setupFixture({
      chapters: [chapterWithStableFrames],
      storyboards: [storyboardAsset("board-952-first", 0), storyboardAsset("board-952-target", 1)],
    });
    expect(writeChatContextPreference({
      apiBaseUrl: fixture.services.apiBaseUrl,
      userId: "actor-952",
      seriesId: series.id,
    }, { kind: "frame", chapterId: chapter.id, storyboardAssetId: "removed-board-952" })).toBe("saved");

    renderPage(fixture.services);

    await screen.findByText("保存的分镜 ID 在当前章节内容中不存在，已回退到整章会话；请重新选择。");
    expect(screen.getByLabelText("会话目标")).toHaveValue("chapter");
    expect(fixture.calls.some((call) => call.method === "GET" && call.url.searchParams.has("frame_index"))).toBe(false);
  });

  it("keeps an explicit chapter choice made while the saved target directory is still loading", async () => {
    const user = userEvent.setup();
    let resolveSavedDirectory!: (assets: StoryboardAsset[]) => void;
    const savedDirectory = new Promise<StoryboardAsset[]>((resolve) => { resolveSavedDirectory = resolve; });
    const fixture = setupFixture({ chapters: [chapterWithStableFrames, alternateChapter] });
    fixture.services = {
      ...fixture.services,
      listStoryboardAssets: vi.fn(async (_seriesId, chapterId) => chapterId === chapter.id ? savedDirectory : []),
    };
    expect(writeChatContextPreference({
      apiBaseUrl: fixture.services.apiBaseUrl,
      userId: "actor-952",
      seriesId: series.id,
    }, { kind: "frame", chapterId: chapter.id, storyboardAssetId: "board-952-target" })).toBe("saved");

    renderPage(fixture.services);
    const chapterSelect = await screen.findByLabelText("章节");
    await waitFor(() => expect(chapterSelect).toBeEnabled());
    await user.selectOptions(chapterSelect, alternateChapter.id);
    expect(chapterSelect).toHaveValue(alternateChapter.id);
    expect(screen.getByLabelText("会话目标")).toHaveValue("chapter");

    resolveSavedDirectory([storyboardAsset("board-952-target", 1)]);
    await waitFor(() => expect(fixture.calls.some((call) => call.method === "GET"
      && call.url.pathname.includes(`/chapters/${alternateChapter.id}/chat-messages`))).toBe(true));
    expect(screen.getByLabelText("会话目标")).toHaveValue("chapter");
    expect(fixture.calls.some((call) => call.method === "GET" && call.url.searchParams.has("frame_index"))).toBe(false);
  });

  it("preserves the latest explicit chapter choice when services refresh in the same scope", async () => {
    const user = userEvent.setup();
    const fixture = setupFixture({ chapters: [chapter, alternateChapter] });
    const rendered = renderPage(fixture.services);
    const chapterSelect = await screen.findByLabelText("章节");
    await waitFor(() => expect(chapterSelect).toBeEnabled());
    await user.selectOptions(chapterSelect, alternateChapter.id);

    const refreshedServices = {
      ...fixture.services,
      listChapters: vi.fn(async () => [chapter, alternateChapter]),
    };
    rendered.rerenderWithServices(refreshedServices);
    await waitFor(() => expect(refreshedServices.listChapters).toHaveBeenCalled());
    expect(screen.getByLabelText("章节")).toHaveValue(alternateChapter.id);
    expect(screen.getByLabelText("会话目标")).toHaveValue("chapter");
  });

  it("releases an invalidated stream on same-scope service refresh without discarding its pending locator", async () => {
    const user = userEvent.setup();
    const fixture = setupFixture();
    const originalPort = fixture.services.chatAssistant;
    if (originalPort === undefined) throw new Error("fixture chat service is unavailable");
    let resolveStreamStarted!: () => void;
    const streamStarted = new Promise<void>((resolve) => { resolveStreamStarted = resolve; });
    let streamAborted = false;
    const controlledPort: NonNullable<WorkspaceServices["chatAssistant"]> = {
      beginRun(context) {
        const run = originalPort.beginRun(context);
        return {
          ...run,
          stream: async function* (_input, signal) {
            resolveStreamStarted();
            await new Promise<void>((resolve) => {
              if (signal.aborted) {
                streamAborted = true;
                resolve();
                return;
              }
              signal.addEventListener("abort", () => {
                streamAborted = true;
                resolve();
              }, { once: true });
            });
            yield { type: "done" };
          },
        };
      },
    };
    const initialServices = { ...fixture.services, chatAssistant: controlledPort };
    const rendered = renderPage(initialServices);
    const modelSelect = await screen.findByLabelText("文本模型");
    await waitFor(() => expect(modelSelect).toBeEnabled());
    await user.selectOptions(modelSelect, model.id);
    await user.type(screen.getByLabelText("发送给 AI 助手"), "服务刷新中的原任务");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await streamStarted;

    const refreshedServices = {
      ...initialServices,
      listChapters: vi.fn(async () => [chapter]),
    };
    rendered.rerenderWithServices(refreshedServices);

    await screen.findByText("聊天服务已更新；旧流已停止，可刷新原任务状态。");
    await waitFor(() => expect(streamAborted).toBe(true));
    expect(screen.getByRole("button", { name: "刷新原任务状态" })).toBeEnabled();
    expect(window.sessionStorage.getItem("haoai.chat-assistant.pending.v1:actor-952:series-952")).not.toBeNull();
    expect(fixture.calls.filter((call) => call.method === "POST" && call.url.pathname.endsWith("/chat-messages"))).toHaveLength(2);
    expect(fixture.calls.filter((call) => call.method === "POST" && call.url.pathname === "/api/chat/stream")).toHaveLength(0);
  });

  it("preserves a draft and cancels target restoration when the user starts typing", async () => {
    const user = userEvent.setup();
    let resolveSavedDirectory!: (assets: StoryboardAsset[]) => void;
    const savedDirectory = new Promise<StoryboardAsset[]>((resolve) => { resolveSavedDirectory = resolve; });
    const fixture = setupFixture({ chapters: [chapterWithStableFrames] });
    fixture.services = {
      ...fixture.services,
      listStoryboardAssets: vi.fn(async () => savedDirectory),
    };
    expect(writeChatContextPreference({
      apiBaseUrl: fixture.services.apiBaseUrl,
      userId: "actor-952",
      seriesId: series.id,
    }, { kind: "frame", chapterId: chapter.id, storyboardAssetId: "board-952-target" })).toBe("saved");

    renderPage(fixture.services);
    const draft = await screen.findByLabelText("发送给 AI 助手");
    await waitFor(() => expect(fixture.services.listStoryboardAssets).toHaveBeenCalled());
    await user.type(draft, "写到一半的草稿");
    resolveSavedDirectory([storyboardAsset("board-952-first", 0), storyboardAsset("board-952-target", 1)]);

    await waitFor(() => expect(screen.getByLabelText("会话目标")).toHaveValue("chapter"));
    expect(draft).toHaveValue("写到一半的草稿");
    expect(fixture.calls.some((call) => call.method === "GET" && call.url.searchParams.has("frame_index"))).toBe(false);
  });

  it("keeps manual chat available when context storage reads fail", async () => {
    const user = userEvent.setup();
    const fixture = setupFixture();
    const originalGetItem = Storage.prototype.getItem;
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(function getItem(this: Storage, key: string) {
      if (key.startsWith("haoai:chat-assistant-context:v1:")) throw new Error("storage unavailable");
      return originalGetItem.call(this, key);
    });

    renderPage(fixture.services);
    await screen.findByText("浏览器存储不可用，未能读取上次聊天目标；当前会话仍可手动使用。");
    await user.selectOptions(await screen.findByLabelText("文本模型"), model.id);
    await user.type(screen.getByLabelText("发送给 AI 助手"), "存储读取失败仍可聊天");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await screen.findByText("助手回答：你好");
    expect(fixture.calls.filter((call) => call.method === "POST" && call.url.pathname === "/api/chat/stream")).toHaveLength(1);
  });

  it("keeps manual chat available when saving the context preference fails", async () => {
    const user = userEvent.setup();
    const fixture = setupFixture({ chapters: [chapter, alternateChapter] });
    const originalSetItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function setItem(this: Storage, key: string, value: string) {
      if (key.startsWith("haoai:chat-assistant-context:v1:")) throw new Error("storage unavailable");
      originalSetItem.call(this, key, value);
    });

    renderPage(fixture.services);
    const chapterSelect = await screen.findByLabelText("章节");
    await waitFor(() => expect(chapterSelect).toBeEnabled());
    await user.selectOptions(chapterSelect, alternateChapter.id);
    await screen.findByText("浏览器存储不可用，当前聊天目标未持久化；当前会话仍可手动使用。");
    await user.selectOptions(await screen.findByLabelText("文本模型"), model.id);
    await user.type(screen.getByLabelText("发送给 AI 助手"), "存储写入失败仍可聊天");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await screen.findByText("助手回答：你好");
    expect(fixture.streamBody?.messages).toMatchObject([
      { role: "user", content: "存储写入失败仍可聊天" },
    ]);
  });

  it("ignores a late history 401 after the actor and session change", async () => {
    const fixture = setupFixture();
    const originalImplementation = fixture.fetcher.getMockImplementation();
    if (originalImplementation === undefined) throw new Error("fixture fetcher has no implementation");
    let resolveOldHistory!: (response: Response) => void;
    const oldHistory = new Promise<Response>((resolve) => { resolveOldHistory = resolve; });
    let historyRequests = 0;
    fixture.fetcher.mockImplementation(async (input, init) => {
      const url = new URL(String(input));
      if (url.pathname.endsWith("/chat-messages") && (init?.method ?? "GET") === "GET") {
        historyRequests += 1;
        return historyRequests === 1 ? oldHistory : Response.json([]);
      }
      return originalImplementation(input, init);
    });
    const rendered = renderPage(fixture.services);
    await waitFor(() => expect(historyRequests).toBe(1));

    fixture.storage.setItem(API_USER_KEY, JSON.stringify({ id: "actor-next" }));
    rendered.rerenderWithServices(fixture.services, { userId: "actor-next", sessionGeneration: 2 });
    await waitFor(() => expect(historyRequests).toBe(2));
    resolveOldHistory(Response.json({ detail: "expired old session" }, { status: 401 }));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(rendered.onUnauthorized).not.toHaveBeenCalled();
    expect(screen.queryByText("expired old session")).not.toBeInTheDocument();
  });

  it("persists server messages once, streams with the selected template, and verifies the terminal task", async () => {
    const user = userEvent.setup();
    const fixture = setupFixture();
    renderPage(fixture.services);

    const modelSelect = await screen.findByLabelText("文本模型");
    await waitFor(() => expect(modelSelect).toBeEnabled());
    await user.selectOptions(modelSelect, model.id);
    await user.selectOptions(screen.getByLabelText("提示词模板"), prompt.id);
    await user.type(screen.getByLabelText("发送给 AI 助手"), "你好");
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    await screen.findByText("助手回答：你好");
    await screen.findByText("聊天已完成，并已核对原任务回执与服务器消息。");
    const messagePosts = fixture.calls.filter((call) => call.method === "POST" && call.url.pathname.endsWith("/chat-messages"));
    const streams = fixture.calls.filter((call) => call.method === "POST" && call.url.pathname === "/api/chat/stream");
    expect(messagePosts).toHaveLength(2);
    expect(streams).toHaveLength(1);
    expect(messagePosts.map((call) => (call.body as Record<string, unknown>).role)).toEqual(["user", "assistant"]);
    expect((messagePosts[0]!.body as Record<string, unknown>).content).toContain("__CTX__:");
    expect(fixture.streamBody).toMatchObject({
      model_id: model.id,
      message_id: fixture.messages[1]!.id,
      prompt_id: prompt.id,
      enable_thinking: false,
      messages: [
        { role: "system", content: prompt.system_prompt },
        { role: "user", content: `${prompt.user_prompt}\n\n你好` },
      ],
    });
    expect(fixture.streamBody).not.toHaveProperty("skip_credit_check");
    expect(fixture.streamBody).not.toHaveProperty("task_type");
    expect(streams[0]!.headers.has("Idempotency-Key")).toBe(false);
    expect(fixture.calls.filter((call) => call.url.pathname === "/api/chat/tasks" && call.url.searchParams.has("message_id")))
      .toHaveLength(2);
  });

  it("restores model and prompt by target category and uses the restored choice in the chat stream", async () => {
    const user = userEvent.setup();
    const fixture = setupFixture({ models: [model, reasoningModel], prompts: [prompt, alternatePrompt] });
    window.localStorage.clear();
    renderPage(fixture.services);

    const modelSelect = await screen.findByLabelText("文本模型");
    const promptSelect = await screen.findByLabelText("提示词模板");
    const targetSelect = await screen.findByLabelText("会话目标");
    await waitFor(() => expect(modelSelect).toBeEnabled());
    await user.selectOptions(modelSelect, model.id);
    await user.selectOptions(promptSelect, prompt.id);

    await user.selectOptions(targetSelect, "frame:0");
    await waitFor(() => {
      expect(modelSelect).toBeEnabled();
      expect(modelSelect).toHaveValue("");
      expect(promptSelect).toHaveValue("");
    });
    await user.selectOptions(modelSelect, reasoningModel.id);
    await user.selectOptions(promptSelect, alternatePrompt.id);

    await user.selectOptions(targetSelect, "chapter");
    await waitFor(() => {
      expect(modelSelect).toHaveValue(model.id);
      expect(promptSelect).toHaveValue(prompt.id);
    });
    await user.selectOptions(targetSelect, "frame:0");
    await waitFor(() => {
      expect(modelSelect).toHaveValue(reasoningModel.id);
      expect(promptSelect).toHaveValue(alternatePrompt.id);
    });

    await user.type(screen.getByLabelText("发送给 AI 助手"), "继续分镜讨论");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await screen.findByText("聊天已完成，并已核对原任务回执与服务器消息。");
    expect(fixture.streamBody).toMatchObject({
      model_id: reasoningModel.id,
      prompt_id: alternatePrompt.id,
      messages: [
        { role: "system", content: alternatePrompt.system_prompt },
        { role: "user", content: `${alternatePrompt.user_prompt}\n\n继续分镜讨论` },
      ],
    });
  });

  it("keeps a manually selected model and prompt across a catalog refresh when local storage fails", async () => {
    const user = userEvent.setup();
    const fixture = setupFixture({ models: [model, reasoningModel], prompts: [prompt, alternatePrompt] });
    const preferenceScope = { apiBaseUrl: "http://127.0.0.1:4175/api", userId: "actor-952" };
    expect(writeChatCategoryPreference(preferenceScope, "general", {
      modelId: model.id,
      promptId: prompt.id,
    })).toBe(true);
    const rendered = renderPage(fixture.services);
    const modelSelect = await screen.findByLabelText("文本模型");
    const promptSelect = await screen.findByLabelText("提示词模板");
    await waitFor(() => expect(modelSelect).toBeEnabled());
    expect(modelSelect).toHaveValue(model.id);
    expect(promptSelect).toHaveValue(prompt.id);

    const originalSetItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation((key, value) => {
      if (key.startsWith("haoai:chat-assistant-selection:v1:")) throw new Error("storage unavailable");
      originalSetItem.call(window.localStorage, key, value);
    });
    await user.selectOptions(modelSelect, reasoningModel.id);
    await user.selectOptions(promptSelect, alternatePrompt.id);
    expect(await screen.findByText("浏览器未能保存此分类的聊天偏好；当前选择仍可用于本次对话。")).toBeInTheDocument();

    rendered.rerenderWithServices({
      ...fixture.services,
      storyboardGeneration: {
        ...fixture.services.storyboardGeneration!,
        listModels: vi.fn(async () => [model, reasoningModel]),
      },
      creatorSettings: {
        ...fixture.services.creatorSettings!,
        listPrompts: vi.fn(async (category) => category === "chat-assistant" ? [prompt, alternatePrompt] : []),
      },
    });
    await waitFor(() => {
      expect(modelSelect).toHaveValue(reasoningModel.id);
      expect(promptSelect).toHaveValue(alternatePrompt.id);
    });
    expect(screen.getByText("浏览器未能保存此分类的聊天偏好；当前选择仍可用于本次对话。")).toBeInTheDocument();

    await user.type(screen.getByLabelText("发送给 AI 助手"), "仍可按当前选择发送");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await screen.findByText("聊天已完成，并已核对原任务回执与服务器消息。");
    expect(fixture.streamBody).toMatchObject({ model_id: reasoningModel.id, prompt_id: alternatePrompt.id });
  });

  it("recovers a disconnected stream and never streams after an unknown assistant-message create", async () => {
    const user = userEvent.setup();
    const fixture = setupFixture({ streamMode: "disconnect" });
    renderPage(fixture.services);

    const modelSelect = await screen.findByLabelText("文本模型");
    await waitFor(() => expect(modelSelect).toBeEnabled());
    await user.selectOptions(modelSelect, model.id);
    await user.type(screen.getByLabelText("发送给 AI 助手"), "继续");
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    await screen.findByText("断流后仍完成");
    await screen.findByText("聊天已完成，并已核对原任务回执与服务器消息。");
    expect(fixture.calls.filter((call) => call.method === "POST" && call.url.pathname.endsWith("/chat-messages"))).toHaveLength(2);
    expect(fixture.calls.filter((call) => call.method === "POST" && call.url.pathname === "/api/chat/stream")).toHaveLength(1);
    expect(fixture.calls.some((call) => call.method === "GET" && call.url.pathname === "/api/chat/tasks"
      && call.url.searchParams.get("message_id") === fixture.messages[1]?.id)).toBe(true);
    await waitFor(() => expect(fixture.calls.filter((call) => call.method === "POST" && call.url.pathname.endsWith("/chat-messages"))).toHaveLength(2));

    cleanup();
    window.sessionStorage.clear();
    const unknownCreateUser = userEvent.setup();
    const unknownCreate = setupFixture({ assistantCreateResponseLost: true });
    renderPage(unknownCreate.services);
    await unknownCreateUser.selectOptions(await screen.findByLabelText("文本模型"), model.id);
    await unknownCreateUser.type(screen.getByLabelText("发送给 AI 助手"), "不要误绑占位");
    await unknownCreateUser.click(screen.getByRole("button", { name: "发送消息" }));

    await screen.findByText("助手占位消息创建结果尚未确认；系统只读刷新了会话，不会猜测消息编号或启动聊天流。");
    expect(unknownCreate.calls.filter((call) => call.method === "POST" && call.url.pathname.endsWith("/chat-messages")))
      .toHaveLength(2);
    expect(unknownCreate.calls.filter((call) => call.method === "POST" && call.url.pathname === "/api/chat/stream"))
      .toHaveLength(0);
  });

  it("renders source text safely and re-edits a frame message without sending a request", async () => {
    const user = userEvent.setup();
    const clipboard = vi.fn(async (_text: string) => undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: clipboard },
    });
    const body = "<img src=x onerror=alert(1)>";
    const userMessage = message({
      id: "old-user-952",
      role: "user",
      content: `__CTX__:${JSON.stringify({ modelId: model.id, modelName: model.name, promptId: prompt.id })}\n${body}`,
      frameIndex: 0,
      createdAt: "2026-10-26T00:00:00Z",
    });
    const assistantMessage = message({
      id: "old-assistant-952",
      role: "assistant",
      content: "<script>alert('x')</script>",
      frameIndex: 0,
      modelName: model.name,
      createdAt: "2026-10-26T00:00:01Z",
    });
    const fixture = setupFixture({
      initialMessages: [userMessage, assistantMessage],
      initialTasks: [taskReceipt(assistantMessage.id, assistantMessage.content)],
    });
    const onNavigateSource = vi.fn<(target: ChatAssistantSourceIntent) => void>();
    renderPage(fixture.services, onNavigateSource);

    const targetSelect = await screen.findByLabelText("会话目标");
    await waitFor(() => expect(targetSelect).toBeEnabled());
    await user.selectOptions(targetSelect, "frame:0");
    await screen.findByText(body);
    expect(screen.getByText("<script>alert('x')</script>")).toBeInTheDocument();
    expect(document.querySelector("img, script")).toBeNull();

    const userBubble = screen.getByText(body).closest("li");
    expect(userBubble).not.toBeNull();
    await user.click(within(userBubble as HTMLElement).getByRole("button", { name: "复制消息" }));
    await screen.findByText("已复制当前可见消息文本。");
    expect(clipboard).toHaveBeenCalledExactlyOnceWith(body);
    await user.click(within(userBubble as HTMLElement).getByRole("button", { name: "打开原章节（历史第 1 镜）" }));
    expect(onNavigateSource).toHaveBeenCalledOnce();
    expect(onNavigateSource.mock.calls[0]![0]).toMatchObject({ kind: "chapter", seriesId: series.id, chapterId: chapter.id });
    expect(onNavigateSource.mock.calls[0]![0].isCurrent()).toBe(true);

    await user.click(within(userBubble as HTMLElement).getByRole("button", { name: "重新编辑" }));
    expect(screen.getByLabelText("发送给 AI 助手")).toHaveValue(body);
    expect(screen.getByLabelText("文本模型")).toHaveValue(model.id);
    expect(screen.getByLabelText("提示词模板")).toHaveValue(prompt.id);
    expect(fixture.calls.filter((call) => call.method === "POST")).toHaveLength(0);
  });

  it("lets the user stop tracking a missing task and send later with a new assistant message id", async () => {
    const user = userEvent.setup();
    const missingAssistantId = "missing-task-assistant-952";
    const fixture = setupFixture({
      initialMessages: [message({ id: missingAssistantId, role: "assistant", content: "..." })],
    });
    window.sessionStorage.setItem("haoai.chat-assistant.pending.v1:actor-952:series-952", JSON.stringify({
      actorId: "actor-952",
      apiBaseUrl: "http://127.0.0.1:4175/api",
      seriesId: series.id,
      target: { kind: "chapter", chapterId: chapter.id },
      assistantMessageId: missingAssistantId,
      taskId: null,
    }));
    expect(writeChatContextPreference({
      apiBaseUrl: fixture.services.apiBaseUrl,
      userId: "actor-952",
      seriesId: series.id,
    }, { kind: "frame", chapterId: chapter.id, storyboardAssetId: "stale-board-952" })).toBe("saved");
    renderPage(fixture.services);

    await screen.findByText("仍未取得终态回执；可稍后手动刷新，系统不会重发。");
    expect(screen.getByLabelText("会话目标")).toHaveValue("chapter");
    const refreshButton = screen.getByRole("button", { name: "刷新原任务状态" });
    await user.click(refreshButton);
    await screen.findByText("仍未取得终态回执；可稍后手动刷新，系统不会重发。");
    expect(fixture.calls.filter((call) => call.method === "GET" && call.url.pathname === "/api/chat/tasks"
      && call.url.searchParams.get("message_id") === missingAssistantId).length).toBeGreaterThanOrEqual(2);

    await user.click(await screen.findByRole("button", { name: "停止跟踪本次任务" }));
    await screen.findByText(/已停止本地跟踪.*费用状态未确认/);
    expect(window.sessionStorage.getItem("haoai.chat-assistant.pending.v1:actor-952:series-952")).toBeNull();
    expect(screen.queryByRole("button", { name: "刷新原任务状态" })).not.toBeInTheDocument();
    expect(fixture.calls.filter((call) => call.method === "POST")).toHaveLength(0);
    expect(fixture.calls.some((call) => call.method === "DELETE")).toBe(false);
    expect(screen.getByLabelText("章节")).toBeEnabled();
    expect(screen.getByLabelText("会话目标")).toBeEnabled();
    expect(screen.getByLabelText("文本模型")).toBeEnabled();
    expect(screen.getByLabelText("发送给 AI 助手")).toBeEnabled();

    await user.selectOptions(screen.getByLabelText("文本模型"), model.id);
    await user.type(screen.getByLabelText("发送给 AI 助手"), "开始新的明确请求");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await screen.findByText("助手回答：你好");

    const newAssistant = fixture.messages.find((row) => row.role === "assistant" && row.id !== missingAssistantId);
    expect(newAssistant).toBeDefined();
    expect(newAssistant!.id).not.toBe(missingAssistantId);
    expect(fixture.streamBody?.message_id).toBe(newAssistant!.id);
    expect(fixture.calls.filter((call) => call.method === "POST" && call.url.pathname === "/api/chat/stream")).toHaveLength(1);
  });

  it("preserves the current pending locator through a same-scope services refresh", async () => {
    const missingAssistantId = "pending-refresh-assistant-952";
    const fixture = setupFixture({ initialMessages: [message({ id: missingAssistantId, role: "assistant", content: "..." })] });
    window.sessionStorage.setItem("haoai.chat-assistant.pending.v1:actor-952:series-952", JSON.stringify({
      actorId: "actor-952",
      apiBaseUrl: "http://127.0.0.1:4175/api",
      seriesId: series.id,
      target: { kind: "chapter", chapterId: chapter.id },
      assistantMessageId: missingAssistantId,
      taskId: null,
    }));
    const rendered = renderPage(fixture.services);
    await screen.findByText("仍未取得终态回执；可稍后手动刷新，系统不会重发。");

    const refreshedServices = {
      ...fixture.services,
      listChapters: vi.fn(async () => [chapter]),
    };
    rendered.rerenderWithServices(refreshedServices);
    await waitFor(() => expect(refreshedServices.listChapters).toHaveBeenCalled());
    expect(screen.getByText("仍未取得终态回执；可稍后手动刷新，系统不会重发。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新原任务状态" })).toBeEnabled();
    expect(window.sessionStorage.getItem("haoai.chat-assistant.pending.v1:actor-952:series-952")).not.toBeNull();
  });

  it("keeps a newer pending refresh busy when an invalidated refresh finishes late", async () => {
    const user = userEvent.setup();
    const missingAssistantId = "pending-refresh-owner-952";
    const fixture = setupFixture({ initialMessages: [message({ id: missingAssistantId, role: "assistant", content: "..." })] });
    window.sessionStorage.setItem("haoai.chat-assistant.pending.v1:actor-952:series-952", JSON.stringify({
      actorId: "actor-952",
      apiBaseUrl: "http://127.0.0.1:4175/api",
      seriesId: series.id,
      target: { kind: "chapter", chapterId: chapter.id },
      assistantMessageId: missingAssistantId,
      taskId: null,
    }));
    const rendered = renderPage(fixture.services);
    await screen.findByText("仍未取得终态回执；可稍后手动刷新，系统不会重发。");

    let releaseOldLookup!: () => void;
    let releaseNewLookup!: () => void;
    let markOldLookupStarted!: () => void;
    let markNewLookupStarted!: () => void;
    let markOldLookupReturned!: () => void;
    const oldGate = new Promise<void>((resolve) => { releaseOldLookup = resolve; });
    const newGate = new Promise<void>((resolve) => { releaseNewLookup = resolve; });
    const oldLookupStarted = new Promise<void>((resolve) => { markOldLookupStarted = resolve; });
    const newLookupStarted = new Promise<void>((resolve) => { markNewLookupStarted = resolve; });
    const oldLookupReturned = new Promise<void>((resolve) => { markOldLookupReturned = resolve; });
    const baseFetcher = fixture.fetcher.getMockImplementation();
    if (baseFetcher === undefined) throw new Error("Fixture fetcher implementation is unavailable.");
    let heldLookups = 0;
    fixture.fetcher.mockImplementation(async (input, init) => {
      const url = new URL(String(input));
      let heldLookup = 0;
      if (url.pathname === "/api/chat/tasks" && url.searchParams.get("message_id") === missingAssistantId) {
        heldLookup = ++heldLookups;
        if (heldLookup === 1) {
          markOldLookupStarted();
          await oldGate;
        } else if (heldLookup === 2) {
          markNewLookupStarted();
          await newGate;
        }
      }
      const response = await baseFetcher(input, init);
      if (heldLookup === 1) markOldLookupReturned();
      return response;
    });

    await user.click(screen.getByRole("button", { name: "刷新原任务状态" }));
    await oldLookupStarted;

    const refreshedServices = {
      ...fixture.services,
      listChapters: vi.fn(async () => [chapter]),
    };
    rendered.rerenderWithServices(refreshedServices);
    await waitFor(() => expect(refreshedServices.listChapters).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByRole("button", { name: "刷新原任务状态" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "刷新原任务状态" }));
    await newLookupStarted;
    expect(screen.getByRole("button", { name: "刷新原任务状态" })).toBeDisabled();

    releaseOldLookup();
    await oldLookupReturned;
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    await user.type(screen.getByLabelText("发送给 AI 助手"), "x");
    expect(screen.getByRole("button", { name: "刷新原任务状态" })).toBeDisabled();

    releaseNewLookup();
    await screen.findByText("仍未取得终态回执；可稍后手动刷新，系统不会重发。");
    await waitFor(() => expect(screen.getByRole("button", { name: "刷新原任务状态" })).toBeEnabled());
    expect(fixture.calls.filter((call) => call.method === "POST")).toHaveLength(0);
  });
});
