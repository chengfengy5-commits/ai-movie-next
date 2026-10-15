import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter, CreateChapterInput, StoryboardFrame } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { WorkspaceServices } from "../../shared/api/services";
import {
  StoryboardGenerationDialog,
} from "./StoryboardGenerationDialog";
import type {
  StoryboardGenerationServicePort,
  StoryboardModelChoice,
  StoryboardPromptChoice,
  TwoPassEvent,
  TwoPassQuote,
  TwoPassQuoteInput,
} from "./contracts";

afterEach(cleanup);

const model: StoryboardModelChoice = {
  id: "text-model",
  name: "安全模型名",
  category: "text",
  provider: "openai-compatible",
  is_system: true,
  cost_per_call: 12,
};

const prompts: StoryboardPromptChoice[] = [
  { id: "scene-prompt", name: "剧情切分", category: "scene-segmentation", is_system: true },
  { id: "frame-prompt", name: "镜头描述", category: "storyboard", is_system: true },
];

const quote: TwoPassQuote = {
  quote_id: "quote-1",
  operation: "storyboard.two_pass",
  model_id: model.id,
  model_name: model.name,
  model_source: "system",
  unit_price: 12,
  multiplier: 2,
  quoted_amount: 24,
  expires_at: "2026-10-15T10:05:00Z",
};

const receipt: TwoPassEvent = {
  type: "receipt",
  data: {
    submission_id: "submission-1",
    operation: "storyboard.two_pass",
    task_id: "task-1",
    status: "processing",
    quoted_amount: 24,
    billing_status: "charged",
  },
};

function makeChapter(id: string, title: string, content: StoryboardFrame[]): Chapter {
  return {
    id,
    series_id: "series-1",
    title,
    content,
    order: 1,
    created_at: "2026-10-15T00:00:00Z",
    updated_at: "2026-10-15T00:00:00Z",
    lock: null,
  };
}

function createDialogServices(options: {
  stream?: () => AsyncIterable<TwoPassEvent>;
  quoteRequest?: StoryboardGenerationServicePort["quote"];
  getSubmission?: StoryboardGenerationServicePort["getSubmission"];
  getTask?: StoryboardGenerationServicePort["getTask"];
  createChapter?: WorkspaceServices["createChapter"];
  listChapters?: WorkspaceServices["listChapters"];
} = {}) {
  const generation: StoryboardGenerationServicePort = {
    listModels: vi.fn(async (): Promise<StoryboardModelChoice[]> => [model]),
    listPrompts: vi.fn(async () => prompts),
    quote: options.quoteRequest ?? vi.fn(async () => quote),
    stream: vi.fn(() => options.stream?.() ?? (async function* () {
      yield receipt;
      yield {
        type: "complete",
        data: { frames: [{
          text: "生成文字",
          duration: 5,
          original_text: "原文",
          character: "hero",
          scene: "room",
          prop: ["chair"],
          storyboard: ["asset-1"],
          nested: { retained: true },
        }] },
      } as TwoPassEvent;
    })()),
    getSubmission: options.getSubmission ?? vi.fn(async () => ({
      submission_id: "submission-1",
      operation: "storyboard.two_pass" as const,
      task_id: "task-1",
      status: "processing",
      quoted_amount: 24,
      billing_status: "charged",
    })),
    getTask: options.getTask ?? vi.fn(async () => ({
      id: "task-1",
      status: "processing",
      billing_status: "charged",
      quoted_amount: 24,
      result: null,
    })),
  };
  const created = makeChapter("created-1", "新章节", [{ text: "生成文字" }]);
  const currentChapters: Chapter[] = [];
  const services = {
    storyboardGeneration: generation,
    createChapter: options.createChapter ?? vi.fn(async (_seriesId: string, input: CreateChapterInput) => {
      const chapter = makeChapter(created.id, input.title, input.content);
      currentChapters.push(chapter);
      return chapter;
    }),
    listChapters: options.listChapters ?? vi.fn(async () => currentChapters),
    listStoryboardAssets: vi.fn(async () => []),
  } as unknown as WorkspaceServices;
  return { generation, services, currentChapters };
}

function renderDialog(services: WorkspaceServices, onCreated = vi.fn()) {
  return render(
    <StoryboardGenerationDialog
      onClose={vi.fn()}
      onCreated={onCreated}
      onUnauthorized={vi.fn()}
      seriesId="series-1"
      seriesName="测试剧集"
      services={services}
      userId="user-1"
    />,
  );
}

async function submitQuoteAndGenerate(user = userEvent.setup()) {
  await screen.findByRole("option", { name: /安全模型名/ });
  await user.type(screen.getByPlaceholderText("粘贴要生成分镜的剧本文字"), "本集剧情");
  await user.type(screen.getByLabelText("新章节标题"), "新章节");
  await user.click(screen.getByRole("button", { name: "获取报价" }));
  await screen.findByText(/服务器报价：24 积分/);
  expect(screen.getByRole("button", { name: /确认报价并生成/ })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: /确认报价并生成/ }));
  return user;
}

describe("StoryboardGenerationDialog", () => {
  it("freezes priced inputs while quoting and submits the quote for the captured input", async () => {
    let resolveQuote!: (value: TwoPassQuote) => void;
    const quoteRequest = vi.fn((_input: TwoPassQuoteInput) => new Promise<TwoPassQuote>((resolve) => {
      resolveQuote = resolve;
    }));
    const { generation, services } = createDialogServices({ quoteRequest });
    generation.listModels = vi.fn(async (): Promise<StoryboardModelChoice[]> => [
      model,
      { ...model, id: "other-model", name: "其他模型" },
    ]);
    renderDialog(services);
    const user = userEvent.setup();
    await screen.findByRole("option", { name: /其他模型/ });
    const script = screen.getByPlaceholderText("粘贴要生成分镜的剧本文字");
    const title = screen.getByLabelText("新章节标题");
    const modelSelect = screen.getByLabelText("生成模型");
    const sceneSelect = screen.getByLabelText("剧情分段提示词");
    const frameSelect = screen.getByLabelText("分镜提示词");
    const temperature = screen.getByLabelText("温度");
    const thinking = screen.getByRole("checkbox", { name: "启用推理" });
    const skipFrames = screen.getByRole("checkbox", { name: "仅生成剧情分段" });

    await user.type(script, "锁定剧本");
    await user.type(title, "报价前标题");
    await user.click(screen.getByRole("button", { name: "获取报价" }));
    await screen.findByText("正在获取服务器报价…");

    for (const input of [script, modelSelect, sceneSelect, frameSelect, temperature, thinking, skipFrames]) {
      expect(input).toBeDisabled();
    }
    expect(title).not.toBeDisabled();
    await user.clear(title);
    await user.type(title, "报价后标题");
    await act(async () => resolveQuote({ ...quote, quote_id: "quote-for-locked-input" }));
    await screen.findByText(/服务器报价：24 积分/);
    expect(script).toHaveValue("锁定剧本");
    expect(title).toHaveValue("报价后标题");

    await user.click(screen.getByRole("button", { name: /确认报价并生成/ }));
    await screen.findByText(/生成预览（1 个镜头）/);
    expect(quoteRequest).toHaveBeenCalledWith({
      series_id: "series-1",
      raw_content: "锁定剧本",
      model_id: "text-model",
      scene_prompt_id: "scene-prompt",
      frame_prompt_id: "frame-prompt",
      temperature: 0.7,
      enable_thinking: false,
      skip_frame_generation: false,
    }, expect.any(AbortSignal));
    expect(generation.stream).toHaveBeenCalledWith(
      "quote-for-locked-input",
      expect.any(String),
      expect.any(AbortSignal),
    );
  });

  it("requires an explicit quote confirmation, edits empty text and saves once after fresh directory and assets reads", async () => {
    const { generation, services, currentChapters } = createDialogServices();
    const onCreated = vi.fn();
    renderDialog(services, onCreated);
    const user = await submitQuoteAndGenerate();
    await screen.findByText(/生成预览（1 个镜头）/);
    expect(generation.stream).toHaveBeenCalledTimes(1);

    const frameText = screen.getByLabelText("镜头 1 文字");
    await user.clear(frameText);
    await user.click(screen.getByRole("button", { name: "确认并创建新章节" }));
    await screen.findByText("已保存：新章节");

    const createChapter = services.createChapter as NonNullable<WorkspaceServices["createChapter"]>;
    expect(createChapter).toHaveBeenCalledTimes(1);
    expect(createChapter).toHaveBeenCalledWith("series-1", {
      title: "新章节",
      order: 0,
      content: [{ text: "", character: "hero", scene: "room", prop: ["chair"] }],
    }, expect.any(AbortSignal));
    expect(currentChapters).toHaveLength(1);
    expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({ id: "created-1" }));
  });

  it("only retries directory readback after a confirmed chapter create", async () => {
    const listChapters = vi.fn(async () => [] as Chapter[]);
    listChapters.mockResolvedValueOnce([]);
    listChapters.mockRejectedValueOnce(new ApiError("network", "目录暂不可用"));
    listChapters.mockResolvedValueOnce([makeChapter("created-1", "新章节", [{ text: "生成文字" }])]);
    const { services } = createDialogServices({ listChapters });
    renderDialog(services);
    const user = await submitQuoteAndGenerate();
    await screen.findByText(/生成预览（1 个镜头）/);
    await user.click(screen.getByRole("button", { name: "确认并创建新章节" }));

    await screen.findByRole("button", { name: "重试目录与素材回读" });
    expect(services.createChapter).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "重试目录与素材回读" }));
    await screen.findByText("已保存：新章节");
    expect(services.createChapter).toHaveBeenCalledTimes(1);
    expect(listChapters).toHaveBeenCalledTimes(3);
  });

  it("keeps the script after a 503 and does not fall back to an unquoted generation path", async () => {
    const quoteRequest = vi.fn(async () => {
      throw new ApiError("http", "生成服务暂不可用。", 503);
    });
    const { generation, services } = createDialogServices({ quoteRequest });
    renderDialog(services);
    const user = userEvent.setup();
    await screen.findByRole("option", { name: /安全模型名/ });
    const script = screen.getByPlaceholderText("粘贴要生成分镜的剧本文字");
    await user.type(script, "保留的剧本草稿");
    await user.click(screen.getByRole("button", { name: "获取报价" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("生成服务暂不可用。");
    expect(script).toHaveValue("保留的剧本草稿");
    expect(generation.stream).not.toHaveBeenCalled();
  });

  it("uses recovery GET after an unknown stream outcome and never repeats the generation POST", async () => {
    const stream = vi.fn(() => (async function* () {
      yield receipt;
      throw new ApiError("network", "连接已断开");
    })());
    const getSubmission = vi.fn(async () => ({
      submission_id: "submission-1",
      operation: "storyboard.two_pass" as const,
      task_id: "task-1",
      status: "processing",
      quoted_amount: 24,
      billing_status: "charged",
    }));
    const getTask = vi.fn(async () => ({
      id: "task-1",
      status: "processing",
      billing_status: "charged",
      quoted_amount: 24,
      result: null,
    }));
    const { generation, services } = createDialogServices({ stream, getSubmission, getTask });
    renderDialog(services);
    const user = await submitQuoteAndGenerate();
    await screen.findByRole("button", { name: "查询已提交任务" });
    await user.click(screen.getByRole("button", { name: "查询已提交任务" }));
    await waitFor(() => expect(getTask).toHaveBeenCalledWith("task-1", expect.any(AbortSignal)));
    expect(getSubmission).toHaveBeenCalledTimes(1);
    expect(generation.stream).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/任务状态：processing/)).toBeTruthy();
  });

  it("does not repost a chapter when its create result is unknown and directory readback finds no match", async () => {
    const createChapter = vi.fn(async () => {
      throw new ApiError("network", "连接中断");
    });
    const listChapters = vi.fn(async () => [] as Chapter[]);
    const { services } = createDialogServices({ createChapter, listChapters });
    renderDialog(services);
    const user = await submitQuoteAndGenerate();
    await screen.findByText(/生成预览（1 个镜头）/);
    await user.click(screen.getByRole("button", { name: "确认并创建新章节" }));
    await screen.findByRole("button", { name: "只读回查，不重复创建" });
    await user.click(screen.getByRole("button", { name: "只读回查，不重复创建" }));
    await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(3));
    expect(createChapter).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/仍无法唯一确认新章，保留草稿且不会自动重发/)).toBeTruthy();
  });

  it("ignores late catalog responses after switching series and service scope", async () => {
    let resolveOldModels: ((items: StoryboardModelChoice[]) => void) | undefined;
    const first = createDialogServices();
    first.generation.listModels = vi.fn(() => new Promise<StoryboardModelChoice[]>((resolve) => {
      resolveOldModels = resolve;
    }));
    const second = createDialogServices();
    second.generation.listModels = vi.fn(async (): Promise<StoryboardModelChoice[]> => (
      [{ ...model, id: "new-model", name: "新作用域模型" }]
    ));
    const view = render(
      <StoryboardGenerationDialog
        onClose={vi.fn()}
        onUnauthorized={vi.fn()}
        seriesId="series-1"
        seriesName="测试剧集"
        services={first.services}
        userId="user-1"
      />,
    );
    view.rerender(
      <StoryboardGenerationDialog
        onClose={vi.fn()}
        onUnauthorized={vi.fn()}
        seriesId="series-2"
        seriesName="新剧集"
        services={second.services}
        userId="user-1"
      />,
    );
    await screen.findByRole("option", { name: /新作用域模型/ });
    await act(async () => {
      resolveOldModels?.([{ ...model, id: "old-model", name: "旧作用域模型" }]);
    });
    expect(screen.queryByRole("option", { name: /旧作用域模型/ })).toBeNull();
    expect(screen.getByText("新剧集")).toBeTruthy();
  });
});
