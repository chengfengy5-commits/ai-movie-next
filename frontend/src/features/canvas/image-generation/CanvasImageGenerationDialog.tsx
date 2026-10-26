import { useSeriesDialogFocus } from "../../series/useSeriesDialogFocus";
import type { CanvasImageGenerationReceipt } from "../../../shared/api/canvasImageGeneration";
import type { CanvasOwnedResultsTarget } from "../../../shared/api/canvasOwnedResults";
import "./CanvasImageGenerationDialog.css";

export interface CanvasImageGenerationModelOption {
  readonly id: string;
  readonly name: string;
  readonly sizeOptions: readonly string[];
  readonly defaultSize: string | null;
  readonly sizeConfigurationError: string | null;
}

export interface CanvasImageGenerationPromptTemplateOption {
  readonly id: string;
  readonly name: string;
  readonly category: string;
  readonly systemPrompt: string | null;
  readonly userPrompt: string | null;
}

export interface CanvasImageGenerationIntentView {
  readonly destination: CanvasOwnedResultsTarget;
  readonly idempotencyKey: string;
  readonly modelId: string;
  readonly modelName: string;
  readonly prompt: string;
  readonly size: string | null;
  readonly targetLabel: string;
  readonly state: "unknown" | "not-found";
}

export interface CanvasImageGenerationReceiptView {
  readonly destination: CanvasOwnedResultsTarget;
  readonly modelId: string;
  readonly modelName: string;
  readonly prompt: string;
  readonly size: string | null;
  readonly receipt: CanvasImageGenerationReceipt;
  readonly targetLabel: string;
}

export interface CanvasImageGenerationDialogProps {
  readonly mode: "form" | "intent" | "receipt";
  readonly targetLabel: string;
  readonly models: readonly CanvasImageGenerationModelOption[];
  readonly selectedModelId: string;
  readonly selectedSize: string;
  readonly sizeSelectionInvalid: boolean;
  readonly promptTemplates: readonly CanvasImageGenerationPromptTemplateOption[];
  readonly selectedPromptTemplateId: string;
  readonly promptTemplateSelectionInvalid: boolean;
  readonly prompt: string;
  readonly modelsLoading: boolean;
  readonly promptTemplatesLoading: boolean;
  readonly promptTemplateError: string | null;
  readonly busy: boolean;
  readonly error: string | null;
  readonly message: string | null;
  readonly lockRequired: boolean;
  readonly lockActionAvailable: boolean;
  readonly lockReady: boolean;
  readonly lockPending: boolean;
  readonly pendingIntent: CanvasImageGenerationIntentView | null;
  readonly receipt: CanvasImageGenerationReceiptView | null;
  onClose(): void;
  onSelectModel(modelId: string): void;
  onSelectSize(size: string): void;
  onSelectPromptTemplate(promptTemplateId: string): void;
  onChangePrompt(prompt: string): void;
  onSubmit(): void;
  onLookup(): void;
  onRetry(): void;
  onAcquireLock(): void;
  onStartNew(): void;
}

function taskStatus(status: CanvasImageGenerationReceipt["status"]): string {
  switch (status) {
    case "queued": return "排队中";
    case "processing": return "处理中";
    case "cancelling": return "正在取消";
    case "reconciling": return "状态核对中";
    case "completed": return "已完成";
    case "failed": return "失败";
  }
}

function billingStatus(status: CanvasImageGenerationReceipt["billingStatus"]): string {
  switch (status) {
    case "legacy_unreconciled": return "历史计费状态待核对";
    case "unbilled": return "未计费";
    case "charged": return "已计费";
    case "partially_refunded": return "部分退款";
    case "refunded": return "已退款";
    case "no_charge": return "未产生费用";
  }
}

function receiptOutcome(receipt: CanvasImageGenerationReceipt): string {
  return receipt.status === "completed"
    ? "任务当前状态为已完成；该回执不表示图像已经应用到画布。"
    : receipt.status === "failed"
      ? "任务当前状态为失败；该回执不表示图像已经应用到画布。"
      : "任务已受理；图像尚未应用到画布。";
}

function targetIdentity(target: CanvasOwnedResultsTarget): string {
  if (target.kind === "asset_image") {
    const kind = target.asset_type === "character" ? "角色素材"
      : target.asset_type === "scene" ? "场景素材" : "道具素材";
    return `${kind} · ${target.asset_id}`;
  }
  return "分镜图片";
}

function receiptDetails(view: CanvasImageGenerationReceiptView) {
  const { receipt } = view;
  return (
    <details className="canvas-image-generation-details">
      <summary>查看任务记录</summary>
      <p>提交编号：{receipt.submissionId}</p>
      <p>任务编号：{receipt.taskId}</p>
      <p>消息编号：{receipt.messageId ?? "无"}</p>
      <p>操作：{receipt.operation === "image.single" ? "单张图片" : receipt.operation}</p>
    </details>
  );
}

export function CanvasImageGenerationDialog({
  mode,
  targetLabel,
  models,
  selectedModelId,
  selectedSize,
  sizeSelectionInvalid,
  promptTemplates,
  selectedPromptTemplateId,
  promptTemplateSelectionInvalid,
  prompt,
  modelsLoading,
  promptTemplatesLoading,
  promptTemplateError,
  busy,
  error,
  message,
  lockRequired,
  lockActionAvailable,
  lockReady,
  lockPending,
  pendingIntent,
  receipt,
  onClose,
  onSelectModel,
  onSelectSize,
  onSelectPromptTemplate,
  onChangePrompt,
  onSubmit,
  onLookup,
  onRetry,
  onAcquireLock,
  onStartNew,
}: CanvasImageGenerationDialogProps) {
  const focus = useSeriesDialogFocus({ active: true, canDismiss: true, onDismiss: onClose });
  const selectedModel = models.find((model) => model.id === selectedModelId);
  const sizeConfigurationInvalid = selectedModel !== undefined && selectedModel.sizeConfigurationError !== null;
  const sizeSelectionBlocked = selectedModel !== undefined && selectedModel.sizeOptions.length > 0
    && (sizeSelectionInvalid || !selectedModel.sizeOptions.includes(selectedSize));
  const title = mode === "form" ? "生成图片"
    : mode === "intent" ? "核对原生成任务" : "生成任务状态";

  return (
    <div className="canvas-image-generation-backdrop" role="presentation">
      <section
        aria-labelledby="canvas-image-generation-title"
        aria-modal="true"
        className="canvas-image-generation-dialog"
        ref={focus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <header className="canvas-image-generation-heading">
          <div>
            <p className="eyebrow">{targetLabel}</p>
            <h2 id="canvas-image-generation-title">{title}</h2>
            <p>提交后会创建单张图片任务。任务回执不会自动应用到画布。</p>
          </div>
          <button
            aria-label="关闭生成图片"
            className="canvas-image-generation-close"
            onClick={() => { focus.markDismissed(); onClose(); }}
            type="button"
          >×</button>
        </header>

        {mode === "form" && (
          <form className="canvas-image-generation-form" onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
            <label>
              图片模型
              <select
                aria-label="图片模型"
                disabled={modelsLoading || busy || models.length === 0}
                onChange={(event) => onSelectModel(event.currentTarget.value)}
                value={selectedModelId}
              >
                {models.length === 0 && <option value="">{modelsLoading ? "正在读取可用模型…" : "没有可用的图片模型"}</option>}
                {models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
              </select>
            </label>
            {selectedModel !== undefined && selectedModel.sizeConfigurationError !== null && (
              <p className="canvas-image-generation-error" role="alert">{selectedModel.sizeConfigurationError}</p>
            )}
            {selectedModel !== undefined && selectedModel.sizeOptions.length > 0 && (
              <label>
                图片尺寸
                <select
                  aria-label="图片尺寸"
                  disabled={busy}
                  onChange={(event) => onSelectSize(event.currentTarget.value)}
                  value={selectedSize}
                >
                  {sizeSelectionBlocked && (
                    <option disabled value={selectedSize}>请选择有效尺寸</option>
                  )}
                  {selectedModel.sizeOptions.map((size) => <option key={size} value={size}>{size}</option>)}
                </select>
              </label>
            )}
            {sizeSelectionInvalid && (
              <p className="canvas-image-generation-error" role="alert">
                请选择当前模型提供的有效尺寸，未发送图片任务。
              </p>
            )}
            <label>
              提示词模板
              <select
                aria-label="提示词模板"
                disabled={promptTemplatesLoading || busy}
                onChange={(event) => onSelectPromptTemplate(event.currentTarget.value)}
                value={selectedPromptTemplateId}
              >
                {promptTemplateSelectionInvalid && (
                  <option disabled value={selectedPromptTemplateId}>请选择有效模板</option>
                )}
                <option value="">无（纯画面描述）</option>
                {promptTemplates.map((template) => (
                  <option key={template.id} value={template.id}>{template.name}</option>
                ))}
              </select>
            </label>
            {promptTemplatesLoading && (
              <p className="canvas-image-generation-message" role="status">正在读取提示词模板…</p>
            )}
            {promptTemplateError !== null && (
              <p className="canvas-image-generation-message" role="status">{promptTemplateError}</p>
            )}
            <label>
              提示词
              <span className="canvas-image-generation-prompt-hint">
                留空时使用当前内容；选用模板后可清空描述，仅使用模板。
              </span>
              <textarea
                aria-label="提示词"
                disabled={busy}
                onChange={(event) => onChangePrompt(event.currentTarget.value)}
                rows={6}
                value={prompt}
              />
            </label>
            {models.length === 0 && !modelsLoading && error === null && (
              <p className="canvas-image-generation-message" role="status">没有可用的图片模型，请先在创作者设置中维护图片模型。</p>
            )}
            {lockRequired && (
              <div className="canvas-image-generation-lock" role="status">
                <p>{lockReady ? "已核实当前编辑锁。提交前仍会再次确认。" : "此章节需要有效的编辑锁。可明确取得锁，再再次提交。"}</p>
                {!lockReady && (
                  lockActionAvailable
                    ? <button disabled={busy || lockPending} onClick={onAcquireLock} type="button">
                      {lockPending ? "正在核实编辑锁…" : "取得编辑锁"}
                    </button>
                    : <span>当前无法取得编辑锁，暂不能提交。</span>
                )}
              </div>
            )}
            {error !== null && <p className="canvas-image-generation-error" role="alert">{error}</p>}
            {message !== null && <p className="canvas-image-generation-message" role="status">{message}</p>}
            <footer className="canvas-image-generation-footer">
              <button disabled={busy || modelsLoading || models.length === 0 || selectedModelId === ""
                || sizeConfigurationInvalid || sizeSelectionBlocked} type="submit">
                {busy ? "正在提交…" : "提交单张图片任务"}
              </button>
              <button disabled={busy} onClick={onClose} type="button">取消</button>
            </footer>
          </form>
        )}

        {mode === "intent" && pendingIntent !== null && (
          <div className="canvas-image-generation-pending" role="alert">
            <p>
              {pendingIntent.state === "not-found"
                ? "暂未查到任务记录。之前的请求仍可能稍后完成，原提交内容和请求编号已保留。"
                : "上次提交结果暂时无法确认。原请求可能已经到达服务；请先核对，不会自动重复提交。"}
            </p>
            {error !== null && <p>{error}</p>}
            {message !== null && <p>{message}</p>}
            <details className="canvas-image-generation-details">
              <summary>查看原提交详情</summary>
              <p>目标：{targetIdentity(pendingIntent.destination)}</p>
              <p>模型：{pendingIntent.modelName}</p>
              <p>图片尺寸：{pendingIntent.size ?? "模型默认"}</p>
              <p>提示词：{pendingIntent.prompt}</p>
              <p>模型编号：{pendingIntent.modelId}</p>
              <p>请求编号：{pendingIntent.idempotencyKey}</p>
            </details>
            {pendingIntent.state === "not-found" && lockRequired && (
              <div className="canvas-image-generation-lock" role="status">
                <p>{lockReady ? "已核实当前编辑锁。重新提交前仍会再次确认。" : "重新提交原操作前需要有效的编辑锁。"}</p>
                {!lockReady && (
                  lockActionAvailable
                    ? <button disabled={busy || lockPending} onClick={onAcquireLock} type="button">
                      {lockPending ? "正在核实编辑锁…" : "取得编辑锁"}
                    </button>
                    : <span>当前无法取得编辑锁，暂不能提交。</span>
                )}
              </div>
            )}
            <div className="canvas-image-generation-actions">
              <button disabled={busy} onClick={onLookup} type="button">{busy ? "正在核对…" : "继续核对原操作"}</button>
              {pendingIntent.state === "not-found" && (
                <button disabled={busy} onClick={onRetry} type="button">重新提交原操作</button>
              )}
            </div>
          </div>
        )}

        {mode === "receipt" && receipt !== null && (
          <div className="canvas-image-generation-receipt" role="status">
            <p>{receiptOutcome(receipt.receipt)}</p>
            <p>任务状态：{taskStatus(receipt.receipt.status)}</p>
            <p>图片尺寸：{receipt.size ?? "模型默认"}</p>
            <p>服务器报价：{receipt.receipt.quotedAmount}</p>
            <p>计费状态：{billingStatus(receipt.receipt.billingStatus)}</p>
            {receiptDetails(receipt)}
            {message !== null && <p>{message}</p>}
            {error !== null && <p className="canvas-image-generation-error" role="alert">{error}</p>}
            <footer className="canvas-image-generation-footer">
              <button onClick={onStartNew} type="button">生成另一张图片</button>
              <button onClick={onClose} type="button">关闭</button>
            </footer>
          </div>
        )}
      </section>
    </div>
  );
}
