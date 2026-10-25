import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CanvasImageGenerationReceipt } from "../../../shared/api/canvasImageGeneration";
import {
  CanvasImageGenerationDialog,
  type CanvasImageGenerationDialogProps,
  type CanvasImageGenerationIntentView,
  type CanvasImageGenerationReceiptView,
} from "./CanvasImageGenerationDialog";

const destination = {
  kind: "frame_preview",
  chapter_id: "chapter-a",
  storyboard_asset_id: "board-a",
} as const;

function pendingIntent(state: CanvasImageGenerationIntentView["state"]): CanvasImageGenerationIntentView {
  return {
    destination,
    idempotencyKey: "idempotency-key-a",
    modelId: "image-model-a",
    modelName: "图片模型 A",
    prompt: "保留的原始提示词",
    targetLabel: "分镜 1",
    state,
  };
}

function receiptView(): CanvasImageGenerationReceiptView {
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
    receipt,
    targetLabel: "分镜 1",
  };
}

function renderDialog(overrides: Partial<CanvasImageGenerationDialogProps> = {}) {
  const props: CanvasImageGenerationDialogProps = {
    mode: "form",
    targetLabel: "分镜 1",
    models: [{ id: "image-model-a", name: "图片模型 A" }],
    selectedModelId: "image-model-a",
    prompt: "当前分镜文本",
    modelsLoading: false,
    busy: false,
    error: null,
    message: null,
    lockRequired: false,
    lockActionAvailable: true,
    lockReady: false,
    lockPending: false,
    pendingIntent: null,
    receipt: null,
    onClose: vi.fn(),
    onSelectModel: vi.fn(),
    onChangePrompt: vi.fn(),
    onSubmit: vi.fn(),
    onLookup: vi.fn(),
    onRetry: vi.fn(),
    onAcquireLock: vi.fn(),
    onStartNew: vi.fn(),
    ...overrides,
  };
  return { ...render(<CanvasImageGenerationDialog {...props} />), props };
}

afterEach(() => cleanup());

describe("CanvasImageGenerationDialog", () => {
  it("offers a model select and required prompt without exposing model configuration", () => {
    renderDialog();
    const dialog = within(screen.getByRole("dialog", { name: "生成图片" }));

    expect(dialog.getByRole("combobox", { name: "图片模型" })).toBeTruthy();
    expect(dialog.getByRole("option", { name: "图片模型 A" })).toBeTruthy();
    const prompt = dialog.getByRole("textbox", { name: "提示词" }) as HTMLTextAreaElement;
    expect(prompt.required).toBe(true);
    expect(prompt.value).toBe("当前分镜文本");
    expect(dialog.getByRole("button", { name: "提交单张图片任务" })).toBeEnabled();
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
    renderDialog({ mode: "receipt", receipt: receiptView() });

    expect(screen.getByText("任务当前状态为已完成；该回执不表示图像已经应用到画布。")).toBeTruthy();
    expect(screen.getByText("任务状态：已完成")).toBeTruthy();
    expect(screen.getByText("服务器报价：12")).toBeTruthy();
    expect(screen.getByText("计费状态：未计费")).toBeTruthy();
    expect(screen.queryByText(/图像生成已完成|图片已应用到画布/)).toBeNull();
    const taskId = screen.getByText("任务编号：task-a");
    expect(taskId.closest("details")?.open).toBe(false);
  });
});
