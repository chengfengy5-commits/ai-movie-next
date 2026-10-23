export type FrameOptimizeProvider = "openai" | "deepseek" | "custom";

export interface FrameOptimizeCapabilities {
  operation: "frame.optimize";
  enabled: boolean;
  reason: "not_configured" | "not_ready" | "worker_unavailable" | null;
  supported_providers: FrameOptimizeProvider[];
}

export interface FrameOptimizeFrameInput {
  index: number;
  text: string;
  asset_context: string;
}

export interface FrameOptimizeSubmitInput {
  chapter_id: string;
  model_id: string;
  prompt_id: string;
  enable_thinking: boolean;
  concurrency: number;
  chapter_texts: string[];
  frames: FrameOptimizeFrameInput[];
}

export interface FrameOptimizeSubmitReceipt {
  task_id: string;
  chapter_id: string;
  frame_count: number;
}

export interface FrameOptimizeRunningTask {
  task_id: string;
  status: string;
  progress: number;
  progress_message: string;
  created_at: string | null;
}

export interface FrameOptimizeTaskResultEntry {
  index: number;
  optimized_text: string;
}

export interface FrameOptimizeTaskStatus {
  task_id: string;
  status: string;
  progress: number;
  progress_message: string;
  /** The legacy endpoint returns arrays; stopped tasks can return a partial/error object. */
  result: FrameOptimizeTaskResultEntry[] | Record<string, unknown> | string | null;
}

export interface FrameOptimizeCancelReceipt {
  task_id: string;
  status: "cancelling";
}

export interface FrameOptimizeServicePort {
  getCapabilities(signal: AbortSignal): Promise<FrameOptimizeCapabilities>;
  submit(input: FrameOptimizeSubmitInput, signal: AbortSignal): Promise<FrameOptimizeSubmitReceipt>;
  getRunning(chapterId: string, signal: AbortSignal): Promise<FrameOptimizeRunningTask | null>;
  getTaskStatus(taskId: string, signal: AbortSignal): Promise<FrameOptimizeTaskStatus>;
  cancel(taskId: string, signal: AbortSignal): Promise<FrameOptimizeCancelReceipt>;
}
