import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Character, Chapter, Prop, Scene } from "../../../shared/api/contracts";
import type { CreatorModel, CreatorPrompt } from "../../../shared/api/creatorSettings";
import { ApiError } from "../../../shared/api/errors";
import {
  type StoryboardAssetAppearance,
  type StoryboardAssetGenerationServicePort,
  type StoryboardAssetKind,
  type StoryboardAssetSaveInput,
  type StoryboardAssetSavePlan,
  type StoryboardAssetStreamEvent,
} from "../../../shared/api/storyboardAssetGeneration";
import type { WorkspaceServices } from "../../../shared/api/services";
import storyboardAssetWire from "../../../testing/fixtures/storyboardAssetWire.json";
import {
  StoryboardAssetExtractionDialog,
  type StoryboardAssetGenerationSnapshot,
} from "./StoryboardAssetExtractionDialog";

const testCases = [
  { kind: "character", label: "角色", promptCategory: "character-extraction" },
  { kind: "scene", label: "场景", promptCategory: "scene-extraction" },
  { kind: "prop", label: "道具", promptCategory: "prop-extraction" },
] as const satisfies ReadonlyArray<{
  kind: StoryboardAssetKind;
  label: string;
  promptCategory: string;
}>;

const unknownAppearanceCases: ReadonlyArray<{
  kind: StoryboardAssetKind;
  label: string;
  description: string;
  appearances: StoryboardAssetAppearance[];
  expected: StoryboardAssetAppearance[];
  preservesOriginal: boolean;
}> = [
  { kind: "character", label: "角色", description: "only unknown character names", appearances: [["陌生角色"], ["陌生角色"]], expected: [null, null], preservesOriginal: true },
  { kind: "character", label: "角色", description: "mixed known and unknown character names", appearances: [["顾宁", "陌生角色"], ["陌生角色", "顾宁"]], expected: [null, null], preservesOriginal: true },
  { kind: "character", label: "角色", description: "explicit empty character selections", appearances: [[], []], expected: [[], []], preservesOriginal: false },
  { kind: "prop", label: "道具", description: "only unknown prop names", appearances: [["陌生道具"], ["陌生道具"]], expected: [null, null], preservesOriginal: true },
  { kind: "prop", label: "道具", description: "mixed known and unknown prop names", appearances: [["旧钥匙", "陌生道具"], ["陌生道具", "旧钥匙"]], expected: [null, null], preservesOriginal: true },
  { kind: "prop", label: "道具", description: "explicit empty prop selections", appearances: [[], []], expected: [[], []], preservesOriginal: false },
];

const seriesId = "series-asset-flow";
const chapterId = "chapter-asset-flow";
const userId = "user-asset-flow";

function event(type: StoryboardAssetStreamEvent["type"], data: Record<string, unknown>): StoryboardAssetStreamEvent {
  return { type, data };
}

function extractedComplete(kind: StoryboardAssetKind): Record<string, unknown> {
  if (kind === "character") return storyboardAssetWire.producer.extract_complete.character;
  if (kind === "scene") return storyboardAssetWire.producer.extract_complete.scene;
  return storyboardAssetWire.producer.extract_complete.prop;
}

function judgedComplete(kind: StoryboardAssetKind): Record<string, unknown> {
  if (kind === "character") return storyboardAssetWire.producer.judge_complete.character;
  if (kind === "scene") return storyboardAssetWire.producer.judge_complete.scene;
  return storyboardAssetWire.producer.judge_complete.prop;
}

function candidateNameFor(kind: StoryboardAssetKind): string {
  if (kind === "character") return "顾宁";
  if (kind === "scene") return "夜色书房";
  return "旧钥匙";
}

function existingAssets(kind: StoryboardAssetKind): Pick<StoryboardAssetGenerationSnapshot, "characters" | "scenes" | "props"> {
  const shared = {
    series_id: seriesId,
    description: "当前剧集素材库中已确认的说明。",
    image_url: null,
    aliases: [],
    canonical_key: null,
    created_at: "2026-10-24T00:00:00Z",
    updated_at: "2026-10-24T00:00:00Z",
  };
  if (kind === "character") {
    const character: Character = {
      ...shared,
      id: "existing-character",
      name: candidateNameFor(kind),
      gender: "女",
      age: "32",
      role: "记者",
      appearance: "短发",
      audio_url: null,
      voice_ref: "清冷",
    };
    return { characters: [character], scenes: [], props: [] };
  }
  if (kind === "scene") {
    const scene: Scene = { ...shared, id: "existing-scene", title: candidateNameFor(kind) };
    return { characters: [], scenes: [scene], props: [] };
  }
  const prop: Prop = { ...shared, id: "existing-prop", name: candidateNameFor(kind) };
  return { characters: [], scenes: [], props: [prop] };
}

function snapshot(kind: StoryboardAssetKind): StoryboardAssetGenerationSnapshot {
  const model: CreatorModel = {
    id: "asset-model",
    category: "reasoning",
    name: "推理文本模型",
    provider: "openai",
    params: null,
    sort_order: 1,
    is_system: false,
    user_id: userId,
  };
  const prompt: CreatorPrompt = {
    id: `prompt-${kind}`,
    category: testCases.find((item) => item.kind === kind)!.promptCategory,
    name: "素材提取提示词",
    system_prompt: "系统指令",
    user_prompt: "用户指令",
    sort_order: 1,
    is_system: false,
    user_id: userId,
  };
  const chapter: Chapter = {
    id: chapterId,
    series_id: seriesId,
    title: "待提取章节",
    content: [
      { text: "镜头一：角色进入房间。" },
      { text: "镜头二：角色取起桌上的东西。" },
      { text: "未选镜头：随后离开。" },
    ],
    order: 0,
    created_at: "2026-10-24T00:00:00Z",
    updated_at: "2026-10-24T00:00:00Z",
    lock: null,
  };
  return {
    chapter,
    assets: [],
    ...existingAssets(kind),
    models: [model],
    prompts: [prompt],
  };
}

function savedPlan(kind: StoryboardAssetKind, input: StoryboardAssetSaveInput): Record<string, unknown> {
  const name = candidateNameFor(kind);
  const candidate = input.candidates.find((item) => {
    const value = kind === "scene" ? item.title ?? item.name : item.name;
    return value === name;
  });
  const existingId = candidate?.existing_id;
  const candidateFrames = input.target_frames.flatMap((frame, slot) => {
    const appearance = input.appearances[slot];
    const names = kind === "scene"
      ? typeof appearance === "string" && appearance !== "" ? [appearance] : []
      : Array.isArray(appearance) ? appearance : [];
    return names.includes(name) ? [frame + 1] : [];
  });
  return {
    asset_type: kind,
    items: [{
      name,
      status: "reuse",
      reason: "",
      frames: candidateFrames,
      existing_id: typeof existingId === "string" ? existingId : null,
      existing_name: name,
      is_existing: true,
      is_backfill: true,
      description: "当前剧集已存在",
      gender: "女",
      age: "32",
      role: "记者",
      appearance: "短发",
      voice_ref: "清冷",
      aliases: [],
    }],
    frame_map: input.target_frames.map((frame, slot) => {
      const appearance = input.appearances[slot];
      const names = kind === "scene"
        ? typeof appearance === "string" && appearance !== "" ? [appearance] : []
        : Array.isArray(appearance) ? appearance : [];
      return { frame: frame + 1, candidates: names };
    }),
  };
}

function makeServices() {
  const streamExtract = vi.fn(async function* (kind: StoryboardAssetKind) {
    yield event("start", { type: "start", message: "开始提取素材..." });
    yield event("status", { type: "status", message: "正在解析素材..." });
    yield event("complete", extractedComplete(kind));
    yield event("done", { type: "done" });
  });
  const streamJudge = vi.fn(async function* (kind: StoryboardAssetKind) {
    const complete = judgedComplete(kind);
    const name = candidateNameFor(kind);
    yield event("start", { type: "start", message: "开始判断出场..." });
    yield event("complete", {
      ...complete,
      appearances: kind === "scene" ? [name, name] : [[name], [name]],
    });
    yield event("done", { type: "done" });
  });
  const streamSave = vi.fn(async function* (kind: StoryboardAssetKind, input: StoryboardAssetSaveInput) {
    const plan = savedPlan(kind, input);
    yield event("start", { type: "start", message: "开始保存素材..." });
    if (input.dry_run) {
      yield event("plan", plan);
      yield event("complete", { type: "complete", success: true, dry_run: true, plan, message: "预览完成" });
    } else {
      yield event("complete", { type: "complete", success: true, message: "保存完成" });
    }
    yield event("done", { type: "done" });
  });
  const generation: StoryboardAssetGenerationServicePort = { streamExtract, streamJudge, streamSave };
  return { generation, streamExtract, streamJudge, streamSave };
}

afterEach(cleanup);

describe("StoryboardAssetExtractionDialog", () => {
  it.each(testCases)("completes the $kind candidate, per-frame plan, and confirmation workflow", async ({ kind, label }) => {
    const user = userEvent.setup();
    const source = snapshot(kind);
    const freshSource: StoryboardAssetGenerationSnapshot = {
      ...source,
      chapter: {
        ...source.chapter,
        content: source.chapter.content?.map((frame) => ({ ...frame })) ?? null,
      },
    };
    const { generation, streamExtract, streamJudge, streamSave } = makeServices();
    const services: Pick<WorkspaceServices, "storyboardAssetGeneration"> = { storyboardAssetGeneration: generation };
    const lifecycle = new AbortController();
    const refreshSource = vi.fn(async () => freshSource);
    const executeConfirmedSave = vi.fn(async (
      _input: StoryboardAssetSaveInput,
      _plan: StoryboardAssetSavePlan,
      _signal: AbortSignal,
      onRequestStarted: () => void,
    ) => {
      onRequestStarted();
    });

    render(
      <StoryboardAssetExtractionDialog
        services={services}
        snapshot={source}
        userId={userId}
        seriesId={seriesId}
        lifecycleSignal={lifecycle.signal}
        isCurrent={() => true}
        refreshSource={refreshSource}
        executeConfirmedSave={executeConfirmedSave}
        onClose={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    );

    const dialog = within(screen.getByRole("dialog", { name: "提取并确认素材" }));
    if (kind !== "character") await user.selectOptions(dialog.getByRole("combobox", { name: "素材类别" }), kind);
    await user.selectOptions(dialog.getByRole("combobox", { name: "提取提示词" }), `prompt-${kind}`);
    await user.click(dialog.getByRole("checkbox", { name: "启用思考" }));
    await user.click(dialog.getByRole("checkbox", { name: "选择镜头 3" }));
    await user.click(dialog.getByRole("button", { name: `提取${label}` }));

    await screen.findByRole("heading", { name: `编辑${label}候选` });
    await waitFor(() => expect(streamExtract).toHaveBeenCalledTimes(1));
    expect(streamExtract).toHaveBeenCalledWith(kind, expect.objectContaining({
      chapter_id: chapterId,
      model_id: "asset-model",
      prompt_id: `prompt-${kind}`,
      enable_thinking: true,
      frame_indices: [0, 1],
    }), expect.any(AbortSignal));

    await user.click(dialog.getByRole("button", { name: "添加候选" }));
    const nameLabel = kind === "scene" ? "场景名称" : kind === "character" ? "角色名称" : "道具名称";
    await user.type(dialog.getAllByLabelText(nameLabel)[1]!, "临时候选");
    const keepToggles = dialog.getAllByRole("checkbox", { name: "纳入本次素材候选" });
    await user.click(keepToggles[1]!);
    const existing = existingAssets(kind);
    const existingId = kind === "character" ? existing.characters[0]!.id : kind === "scene" ? existing.scenes[0]!.id : existing.props[0]!.id;
    await user.selectOptions(dialog.getByRole("combobox", { name: `复用${label}` }), existingId);
    expect(dialog.getByText(`复用当前剧集素材：${existingId}`)).toBeInTheDocument();

    await user.click(dialog.getByRole("button", { name: "判断出场并继续" }));
    await screen.findByRole("heading", { name: "逐镜出场与绑定" });
    await waitFor(() => expect(streamJudge).toHaveBeenCalledTimes(1));
    expect(streamJudge).toHaveBeenCalledWith(kind, expect.objectContaining({
      chapter_id: chapterId,
      model_id: "asset-model",
      prompt_id: `prompt-${kind}`,
      enable_thinking: true,
      skip_merge: true,
      frame_numbers: [1, 2],
      candidates: [expect.objectContaining({ existing_id: existingId, is_existing: true, is_backfill: true })],
    }), expect.any(AbortSignal));

    if (kind === "scene") {
      await user.selectOptions(dialog.getByRole("combobox", { name: "镜头 2 场景绑定" }), "");
    } else {
      const secondFrame = within(dialog.getByRole("article", { name: "镜头 2 出场绑定" }));
      await user.click(secondFrame.getByRole("checkbox", { name: candidateNameFor(kind) }));
    }
    await user.click(dialog.getByRole("button", { name: "生成 dry-run 保存计划" }));
    await screen.findByRole("heading", { name: /保存计划/ });
    await waitFor(() => expect(streamSave).toHaveBeenCalledTimes(1));
    const dryRunInput = streamSave.mock.calls[0]?.[1];
    expect(dryRunInput).toMatchObject({
      chapter_id: chapterId,
      expected_series_id: freshSource.chapter.series_id,
      expected_content: freshSource.chapter.content,
      target_frames: [0, 1],
      dry_run: true,
      candidates: [expect.objectContaining({ existing_id: existingId })],
    });
    expect(dryRunInput?.candidates.some((candidate) => candidate.name === "临时候选")).toBe(false);
    expect(dryRunInput?.appearances).toEqual(kind === "scene" ? [candidateNameFor(kind), ""] : [[candidateNameFor(kind)], []]);

    await user.click(dialog.getByRole("checkbox", { name: /我已核对候选/ }));
    await user.click(dialog.getByRole("button", { name: "明确确认并保存" }));
    await waitFor(() => expect(executeConfirmedSave).toHaveBeenCalledTimes(1));
    expect(executeConfirmedSave).toHaveBeenCalledWith(
      expect.objectContaining({
        chapter_id: chapterId,
        expected_series_id: freshSource.chapter.series_id,
        expected_content: freshSource.chapter.content,
        target_frames: [0, 1],
        appearances: kind === "scene" ? [candidateNameFor(kind), ""] : [[candidateNameFor(kind)], []],
        dry_run: false,
        candidates: [expect.objectContaining({ existing_id: existingId })],
      }),
      expect.objectContaining({ asset_type: kind, items: [expect.objectContaining({ name: candidateNameFor(kind), status: "reuse", existing_id: existingId })] }),
      expect.any(AbortSignal),
      expect.any(Function),
      expect.any(Function),
    );
    expect(await screen.findByText("素材、章节与逐镜绑定已重新读取并核对。" )).toBeInTheDocument();
    expect(refreshSource).toHaveBeenCalledTimes(4);
  });

  it.each(unknownAppearanceCases)("$description preserve the intended per-frame binding for $kind", async ({
    kind,
    label,
    appearances,
    expected,
    preservesOriginal,
  }) => {
    const user = userEvent.setup();
    const source = snapshot(kind);
    const { generation, streamJudge, streamSave } = makeServices();
    streamJudge.mockImplementation(async function* (requestedKind: StoryboardAssetKind) {
      yield event("start", { type: "start", message: "开始判断出场..." });
      yield event("complete", { ...judgedComplete(requestedKind), appearances });
      yield event("done", { type: "done" });
    });
    const services: Pick<WorkspaceServices, "storyboardAssetGeneration"> = { storyboardAssetGeneration: generation };
    render(
      <StoryboardAssetExtractionDialog
        services={services}
        snapshot={source}
        userId={userId}
        seriesId={seriesId}
        lifecycleSignal={new AbortController().signal}
        isCurrent={() => true}
        refreshSource={vi.fn(async () => source)}
        executeConfirmedSave={vi.fn(async () => undefined)}
        onClose={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    );

    const dialog = within(screen.getByRole("dialog", { name: "提取并确认素材" }));
    if (kind !== "character") await user.selectOptions(dialog.getByRole("combobox", { name: "素材类别" }), kind);
    await user.click(dialog.getByRole("checkbox", { name: "选择镜头 3" }));
    await user.click(dialog.getByRole("button", { name: `提取${label}` }));
    await screen.findByRole("heading", { name: `编辑${label}候选` });
    await user.click(dialog.getByRole("button", { name: "判断出场并继续" }));
    await screen.findByRole("heading", { name: "逐镜出场与绑定" });
    if (preservesOriginal) {
      expect(await screen.findByText(/已按“保留原绑定”处理/)).toBeInTheDocument();
    }

    await user.click(dialog.getByRole("button", { name: "生成 dry-run 保存计划" }));
    await screen.findByRole("heading", { name: /保存计划/ });
    await waitFor(() => expect(streamSave).toHaveBeenCalledTimes(1));
    expect(streamSave.mock.calls[0]?.[1]?.appearances).toEqual(expected);
  });

  it("freezes an unknown final save and never resends it after a source refresh", async () => {
    const user = userEvent.setup();
    const source = snapshot("character");
    const { generation, streamSave } = makeServices();
    const services: Pick<WorkspaceServices, "storyboardAssetGeneration"> = { storyboardAssetGeneration: generation };
    const lifecycle = new AbortController();
    const executeConfirmedSave = vi.fn(async (
      _input: StoryboardAssetSaveInput,
      _plan: StoryboardAssetSavePlan,
      _signal: AbortSignal,
      onRequestStarted: () => void,
    ) => {
      onRequestStarted();
      throw new ApiError("network", "保存流中断，回执未知。", null);
    });
    render(
      <StoryboardAssetExtractionDialog
        services={services}
        snapshot={source}
        userId={userId}
        seriesId={seriesId}
        lifecycleSignal={lifecycle.signal}
        isCurrent={() => true}
        refreshSource={vi.fn(async () => source)}
        executeConfirmedSave={executeConfirmedSave}
        onClose={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    );

    const dialog = within(screen.getByRole("dialog", { name: "提取并确认素材" }));
    await user.click(dialog.getByRole("checkbox", { name: "选择镜头 3" }));
    await user.click(dialog.getByRole("button", { name: "提取角色" }));
    await screen.findByRole("heading", { name: "编辑角色候选" });
    await user.click(dialog.getByRole("button", { name: "判断出场并继续" }));
    await screen.findByRole("heading", { name: "逐镜出场与绑定" });
    await user.click(dialog.getByRole("button", { name: "生成 dry-run 保存计划" }));
    await screen.findByRole("heading", { name: /保存计划/ });
    await user.click(dialog.getByRole("checkbox", { name: /我已核对候选/ }));
    await user.click(dialog.getByRole("button", { name: "明确确认并保存" }));

    expect(await screen.findByText(/冻结自动重试/)).toBeInTheDocument();
    expect(executeConfirmedSave).toHaveBeenCalledTimes(1);
    expect(streamSave).toHaveBeenCalledTimes(1);
    await user.click(dialog.getByRole("button", { name: "重新读取真源" }));
    expect(await screen.findByText(/此前保存回执仍未知/)).toBeInTheDocument();
    expect(executeConfirmedSave).toHaveBeenCalledTimes(1);
    expect(streamSave).toHaveBeenCalledTimes(1);
  });

  it("ignores extraction results that arrive after the chapter or session scope changes", async () => {
    const user = userEvent.setup();
    const source = snapshot("character");
    const resultReady = deferred();
    let started = false;
    let current = true;
    const streamExtract = vi.fn(async function* () {
      started = true;
      yield event("start", { type: "start", message: "开始提取素材..." });
      await resultReady.promise;
      yield event("complete", extractedComplete("character"));
      yield event("done", { type: "done" });
    });
    const generation: StoryboardAssetGenerationServicePort = {
      streamExtract,
      streamJudge: vi.fn(async function* () {}),
      streamSave: vi.fn(async function* () {}),
    };
    const services: Pick<WorkspaceServices, "storyboardAssetGeneration"> = { storyboardAssetGeneration: generation };
    const lifecycle = new AbortController();
    const view = render(
      <StoryboardAssetExtractionDialog
        services={services}
        snapshot={source}
        userId={userId}
        seriesId={seriesId}
        lifecycleSignal={lifecycle.signal}
        isCurrent={() => current}
        refreshSource={vi.fn(async () => source)}
        executeConfirmedSave={vi.fn(async () => undefined)}
        onClose={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    );
    const dialog = within(screen.getByRole("dialog", { name: "提取并确认素材" }));

    await user.click(dialog.getByRole("button", { name: "提取角色" }));
    await waitFor(() => expect(started).toBe(true));
    current = false;
    await act(async () => { resultReady.resolve(); });

    expect(screen.queryByRole("heading", { name: "编辑角色候选" })).not.toBeInTheDocument();
    expect(streamExtract).toHaveBeenCalledTimes(1);
    view.unmount();
  });
});

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((complete) => { resolve = complete; });
  return { promise, resolve };
}
