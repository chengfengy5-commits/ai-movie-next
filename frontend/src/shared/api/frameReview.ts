export type FrameReviewProvider = "openai" | "deepseek" | "custom";

export interface FrameReviewCapabilities {
  operation: "frame.review";
  enabled: boolean;
  reason: "not_configured" | "not_ready" | "worker_unavailable" | null;
  supported_providers: FrameReviewProvider[];
}

export interface FrameReviewSubmitInput {
  chapter_id: string;
  frame_index: number;
  prompt_id: string;
  model_id: string;
  message_id: string;
  error_reason: string;
  asset_context: string;
  prev_context: string;
  enable_thinking: boolean;
}

export interface FrameReviewReceipt {
  task_id: string;
  model_name: string;
  is_system: boolean;
  credit_cost: number;
}

export interface FrameReviewTask {
  id: string;
  type: string;
  status: string;
  progress: number | null;
  progress_message: string | null;
  result: string | null;
  credit_cost: number;
  model_name: string | null;
}

export interface FrameReviewResult {
  chapter_id: string;
  frame_index: number;
  prompt_id: string;
  optimized_text: string;
  original_text: string;
}

export interface FrameReviewCancelReceipt {
  task_id: string;
  status: "cancelling";
}

export interface FrameReviewServicePort {
  getCapabilities(signal: AbortSignal): Promise<FrameReviewCapabilities>;
  submit(input: FrameReviewSubmitInput, signal: AbortSignal): Promise<FrameReviewReceipt>;
  getTask(taskId: string, signal: AbortSignal): Promise<FrameReviewTask>;
  cancel(taskId: string, signal: AbortSignal): Promise<FrameReviewCancelReceipt>;
}
