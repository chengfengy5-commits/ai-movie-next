import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CanvasImageGenerationReceipt } from "../../../shared/api/canvasImageGeneration";
import {
  CanvasImageGenerationDialog,
  type CanvasImageGenerationBatchReceiptView,
  type CanvasImageGenerationBatchView,
  type CanvasImageGenerationDialogProps,
  type CanvasImageGenerationIntentView,
} from "./CanvasImageGenerationDialog";

const destination = {
  kind: "frame_preview",
  chapter_id: "chapter-a",
  storyboard_asset_id: "board-a",
} as const;

function pendingIntent(state: CanvasImageGenerationIntentView["state"]): CanvasImageGenerationIntentView {
  const batch: CanvasImageGenerationBatchView = {
    requestedCount: 3,
    referenceNames: ["角色参考"],
    acceptedReceipts: [],
    notSentCount: 1,
    activeOrdinal: null,
    stoppedOrdinal: 2,
    outcome: "unknown",
  };
  return {
    destination,
    idempotencyKey: "idempotency-key-a",
    modelId: "image-model-a",
    modelName: "图片模型 A",
    prompt: "保留的原始提示词",
    size: "quality:high",
    referenceNames: ["角色参考"],
    targetLabel: "分镜 1",
    ordinal: 2,
    state,
    batch,
  };
}

function receiptView(): CanvasImageGenerationBatchReceiptView {
  const receipt: CanvasImageGenerationReceipt = {
    submissionId: "submission-a",
    operation: "image.single",
    taskId: "task-a",
    messageId: null,
    status: "completed",
    quotedAmount: 12,
    billingStatus: "unbilled",
  };
  return {
    destination,
    modelId: "image-model-a",
    modelName: "图片模型 A",
    prompt: "保留的原始提示词",
    size: null,
    referenceNames: [],
    receipt,
    targetLabel: "分镜 1",
    ordinal: 1,
    idempotencyKey: "idempotency-key-a",
  };
}

function renderDialog(overrides: Partial<CanvasImageGenerationDialogProps> = {}) {
  const props: CanvasImageGenerationDialogProps = {
    mode: "form",
    targetLabel: "分镜 1",
    models: [{
      id: "image-model-a",
      name: "图片模型 A",
      sizeOptions: [],
      defaultSize: null,
      sizeConfigurationError: null,
      referenceImagesEnabled: true,
      maximumReferenceImages: 8,
      referenceConfigurationError: null,
    }],
    selectedModelId: "image-model-a",
    selectedCount: 1,
    countSelectionInvalid: false,
    selectedSize: "",
    sizeSelectionInvalid: false,
    promptTemplates: [{
      id: "template-a",
      name: "角色设计",
      category: "character-design",
      systemPrompt: "保持角色一致",
      userPrompt: null,
    }],
    selectedPromptTemplateId: "",
    promptTemplateSelectionInvalid: false,
    prompt: "当前分镜文本",
    referenceCandidates: [],
    selectedReferenceIds: [],
    referenceFiles: [],
    modelsLoading: false,
    promptTemplatesLoading: false,
    promptTemplateError: null,
    busy: false,
    error: null,
    message: null,
    lockRequired: false,
    lockActionAvailable: true,
    lockReady: false,
    lockPending: false,
    pendingIntent: null,
    receipt: null,
    batch: overrides.batch ?? overrides.pendingIntent?.batch ?? null,
    taskObservations: {},
    onClose: vi.fn(),
    onSelectModel: vi.fn(),
    onSelectCount: vi.fn(),
    onSelectSize: vi.fn(),
    onSelectPromptTemplate: vi.fn(),
    onChangePrompt: vi.fn(),
    onToggleReference: vi.fn(),
    onSelectReferenceFiles: vi.fn(),
    onRemoveReferenceFile: vi.fn(),
    onSubmit: vi.fn(),
    onLookup: vi.fn(),
    onRetry: vi.fn(),
    onAcquireLock: vi.fn(),
    onStartNew: vi.fn(),
    onPauseTaskObservation: vi.fn(),
    onContinueTaskObservation: vi.fn(),
    onRefreshTaskObservation: vi.fn(),
    ...overrides,
  };
  return { ...render(<CanvasImageGenerationDialog {...props} />), props };
}

afterEach(() => cleanup());

describe("CanvasImageGenerationDialog", () => {
  it("offers model and optional prompt template selects without exposing model configuration", () => {
    renderDialog();
    const dialog = within(screen.getByRole("dialog", { name: "生成图片" }));

    expect(dialog.getByRole("combobox", { name: "图片模型" })).toBeTruthy();
    expect((dialog.getByRole("combobox", { name: "生成数量" }) as HTMLSelectElement).value).toBe("1");
    expect(Array.from((dialog.getByRole("combobox", { name: "生成数量" }) as HTMLSelectElement).options)
      .map((option) => option.value)).toEqual(["1", "2", "3", "4"]);
    expect(dialog.getByRole("option", { name: "图片模型 A" })).toBeTruthy();
    expect(dialog.getByRole("combobox", { name: "提示词模板" })).toBeTruthy();
    expect(dialog.getByRole("option", { name: "无（纯画面描述）" })).toBeTruthy();
    const prompt = dialog.getByRole("textbox", { name: "提示词" }) as HTMLTextAreaElement;
    expect(prompt.required).toBe(false);
    expect(prompt.value).toBe("当前分镜文本");
    expect(dialog.getByRole("button", { name: "提交单张图片任务" })).toBeEnabled();
  });

  it("keeps template selection separate from the editable prompt", () => {
    const rendered = renderDialog();
    const dialog = within(screen.getByRole("dialog", { name: "生成图片" }));
    const prompt = dialog.getByRole("textbox", { name: "提示词" }) as HTMLTextAreaElement;
    fireEvent.change(dialog.getByRole("combobox", { name: "提示词模板" }), { target: { value: "template-a" } });

    expect(prompt.value).toBe("当前分镜文本");
    expect(rendered.props.onSelectPromptTemplate).toHaveBeenCalledWith("template-a");
    expect(dialog.getByText("留空时使用当前内容；选用模板后可清空描述，仅使用模板。")).toBeTruthy();
  });

  it("offers graph-reference checkboxes and a multiple local-image picker", () => {
    const rendered = renderDialog({
      referenceCandidates: [
        { id: "frame:1", name: "目标分镜", value: "https://images.example.test/frame.png" },
      ],
      selectedReferenceIds: ["frame:1"],
    });
    const dialog = within(screen.getByRole("dialog", { name: "生成图片" }));
    const reference = dialog.getByLabelText("目标分镜") as HTMLInputElement;
    const localPicker = dialog.getByLabelText("选择本地参考图片");
    expect(reference.type).toBe("checkbox");
    expect(reference.checked).toBe(true);
    expect(localPicker).toHaveAttribute("type", "file");
    expect(localPicker).toHaveAttribute("multiple");
    fireEvent.click(reference);
    expect(rendered.props.onToggleReference).toHaveBeenCalledWith("frame:1", false);
  });

  it("blocks an invalid generation count instead of keeping the previous selection", () => {
    const rendered = renderDialog({ selectedCount: 0, countSelectionInvalid: true });
    const dialog = within(screen.getByRole("dialog", { name: "生成图片" }));

    expect(dialog.getByText("请选择1至4张，未发送图片任务。")).toBeTruthy();
    expect(dialog.getByRole("button", { name: "提交0张图片任务" })).toBeDisabled();
    fireEvent.change(dialog.getByRole("combobox", { name: "生成数量" }), { target: { value: "0" } });
    expect(rendered.props.onSelectCount).toHaveBeenCalledWith("0");
  });

  it("offers configured sizes in the form and reports an invalid selection", () => {
    const model = {
      id: "image-model-a",
      name: "图片模型 A",
      sizeOptions: ["16:9", "quality:high"],
      defaultSize: "16:9",
      sizeConfigurationError: null,
      referenceImagesEnabled: true,
      maximumReferenceImages: 8,
      referenceConfigurationError: null,
    };
    const rendered = renderDialog({ models: [model], selectedSize: "16:9" });
    const dialog = within(screen.getByRole("dialog", { name: "生成图片" }));
    const select = dialog.getByRole("combobox", { name: "图片尺寸" }) as HTMLSelectElement;
    expect(select.value).toBe("16:9");
    expect(Array.from(select.options).map((option) => option.value)).toEqual(["16:9", "quality:high"]);
    fireEvent.change(select, { target: { value: "quality:high" } });
    expect(rendered.props.onSelectSize).toHaveBeenCalledWith("quality:high");

    rendered.rerender(<CanvasImageGenerationDialog {...rendered.props} sizeSelectionInvalid />);
    expect(screen.getByText("请选择当前模型提供的有效尺寸，未发送图片任务。")).toBeTruthy();
    expect(screen.getByRole("button", { name: "提交单张图片任务" })).toBeDisabled();
  });

  it("does not expose template controls while inspecting a frozen operation", () => {
    renderDialog({ mode: "intent", pendingIntent: pendingIntent("unknown") });

    expect(screen.queryByRole("combobox", { name: "提示词模板" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "图片尺寸" })).toBeNull();
    expect(screen.getByText("图片尺寸：quality:high")).toBeTruthy();
    expect(screen.getAllByText("参考图（1）：角色参考")).toHaveLength(2);
    expect(screen.getByText("提示词：保留的原始提示词")).toBeTruthy();
  });

  it("keeps unknown submissions read-only until an explicit lookup and same-operation retry", () => {
    const unknown = renderDialog({ mode: "intent", pendingIntent: pendingIntent("unknown") });
    expect(screen.getByRole("button", { name: "继续核对原操作" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "重新提交原操作" })).toBeNull();
    expect(unknown.props.onLookup).not.toHaveBeenCalled();
    expect(unknown.props.onRetry).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "继续核对原操作" }));
    expect(unknown.props.onLookup).toHaveBeenCalledOnce();
    unknown.unmount();

    const notFound = renderDialog({ mode: "intent", pendingIntent: pendingIntent("not-found") });
    expect(screen.getByText(/之前的请求仍可能稍后完成/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新提交原操作" })).toBeEnabled();
    expect(notFound.props.onRetry).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重新提交原操作" }));
    expect(notFound.props.onRetry).toHaveBeenCalledOnce();
  });

  it("shows a server-reported task status without claiming the image was applied", () => {
    const receipt = receiptView();
    renderDialog({
      mode: "receipt",
      receipt,
      batch: {
        requestedCount: 1,
        referenceNames: [],
        acceptedReceipts: [receipt],
        notSentCount: 0,
        activeOrdinal: null,
        stoppedOrdinal: null,
        outcome: "complete",
      },
      taskObservations: {
        [receipt.idempotencyKey]: { status: "stopped", error: null, inFlight: false },
      },
    });

    expect(screen.getByText("任务当前状态为已完成；该回执不表示图像已经应用到画布。")).toBeTruthy();
    expect(screen.getByText("任务状态：已完成")).toBeTruthy();
    expect(screen.getByText("服务器报价：12")).toBeTruthy();
    expect(screen.getByText("图片尺寸：模型默认")).toBeTruthy();
    expect(screen.getByText("计费状态：未计费")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "继续观察第 1 张" })).toBeNull();
    expect(screen.getByRole("button", { name: "刷新第 1 张状态" })).toBeTruthy();
    expect(screen.queryByText(/图像生成已完成|图片已应用到画布/)).toBeNull();
    const taskId = screen.getByText("任务编号：task-a");
    expect(taskId.closest("details")?.open).toBe(false);
  });

  it("keeps accepted task observation controls attached to the original receipt key", () => {
    const accepted = receiptView();
    const rendered = renderDialog({
      mode: "receipt",
      receipt: accepted,
      batch: {
        requestedCount: 1,
        referenceNames: [],
        acceptedReceipts: [accepted],
        notSentCount: 0,
        activeOrdinal: null,
        stoppedOrdinal: null,
        outcome: "complete",
      },
      taskObservations: {
        [accepted.idempotencyKey]: { status: "read-error", error: "安全的只读状态错误。", inFlight: false },
      },
    });

    expect(screen.getByText("状态观察：读取失败，观察已暂停")).toBeTruthy();
    expect(screen.getByText("安全的只读状态错误。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "继续观察第 1 张" }));
    fireEvent.click(screen.getByRole("button", { name: "刷新第 1 张状态" }));
    expect(rendered.props.onContinueTaskObservation).toHaveBeenCalledWith(accepted.idempotencyKey);
    expect(rendered.props.onRefreshTaskObservation).toHaveBeenCalledWith(accepted.idempotencyKey);
  });
});
