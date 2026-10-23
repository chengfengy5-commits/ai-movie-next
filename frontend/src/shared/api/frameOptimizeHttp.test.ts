import { describe, expect, it } from "vitest";
import { ApiError } from "./errors";
import {
  parseFrameOptimizeCancelReceipt,
  parseFrameOptimizeCapabilities,
  parseFrameOptimizeRunning,
  parseFrameOptimizeSubmitReceipt,
  parseFrameOptimizeTaskStatus,
  validateFrameOptimizeSubmitInput,
} from "./frameOptimizeHttp";

describe("frame optimize HTTP contracts", () => {
  it("validates capability state and supported providers", () => {
    expect(parseFrameOptimizeCapabilities({
      operation: "frame.optimize",
      enabled: false,
      reason: "worker_unavailable",
      supported_providers: ["openai", "deepseek", "custom"],
    })).toMatchObject({ enabled: false, reason: "worker_unavailable" });
    expect(() => parseFrameOptimizeCapabilities({
      operation: "frame.review",
      enabled: true,
      reason: null,
      supported_providers: ["openai"],
    })).toThrow(ApiError);
    expect(() => parseFrameOptimizeCapabilities({
      operation: "frame.optimize",
      enabled: true,
      reason: null,
      supported_providers: ["unsupported"],
    })).toThrow(ApiError);
    expect(() => parseFrameOptimizeCapabilities({
      operation: "frame.optimize",
      enabled: true,
      supported_providers: ["openai"],
    })).toThrow(ApiError);
  });

  it("checks the exact submit target and only reads fields present in the wire receipt", () => {
    const input = {
      chapter_id: "chapter-1",
      model_id: "model-1",
      prompt_id: "",
      enable_thinking: false,
      concurrency: 5,
      chapter_texts: ["【剧情片段】"],
      frames: [{ index: 0, text: "原文", asset_context: "" }],
    };
    expect(() => validateFrameOptimizeSubmitInput(input)).not.toThrow();
    expect(() => validateFrameOptimizeSubmitInput({ ...input, prompt_id: null } as never)).toThrow(ApiError);
    expect(parseFrameOptimizeSubmitReceipt({
      task_id: "task-1",
      chapter_id: "chapter-1",
      frame_count: 1,
      credit_cost: 999,
    }, { chapterId: "chapter-1", frameCount: 1 })).toEqual({
      task_id: "task-1",
      chapter_id: "chapter-1",
      frame_count: 1,
    });
    expect(() => validateFrameOptimizeSubmitInput({ ...input, concurrency: 2 })).toThrow(ApiError);
    expect(() => validateFrameOptimizeSubmitInput({
      ...input,
      chapter_texts: ["一", "二"],
      frames: [
        { index: 1, text: "后镜", asset_context: "" },
        { index: 0, text: "前镜", asset_context: "" },
      ],
    })).toThrow(ApiError);
    expect(() => parseFrameOptimizeSubmitReceipt({
      task_id: "task-1",
      chapter_id: "other",
      frame_count: 1,
    }, { chapterId: "chapter-1", frameCount: 1 })).toThrow(ApiError);
  });

  it("accepts unknown running statuses and keeps invalid result strings opaque", () => {
    expect(parseFrameOptimizeRunning({
      task: {
        task_id: "task-1",
        status: "future-status",
        progress: 12.5,
        progress_message: "等待中",
        created_at: null,
      },
    })).toEqual({
      task_id: "task-1",
      status: "future-status",
      progress: 12.5,
      progress_message: "等待中",
      created_at: null,
    });
    expect(() => parseFrameOptimizeRunning({
      task: { task_id: "task-1", status: "queued", progress: 0, created_at: null },
    })).toThrow(ApiError);
    expect(parseFrameOptimizeTaskStatus({
      task_id: "task-1",
      status: "failed",
      progress: 100,
      progress_message: "partial",
      result: "not-json",
    }, "task-1").result).toBe("not-json");
    expect(() => parseFrameOptimizeTaskStatus({
      task_id: "task-2",
      status: "completed",
      progress: 100,
      progress_message: "",
      result: null,
    }, "task-1")).toThrow(ApiError);
  });

  it("requires the authoritative cancellation acknowledgement shape", () => {
    expect(parseFrameOptimizeCancelReceipt({ task_id: "task-1", status: "cancelling" }, "task-1"))
      .toEqual({ task_id: "task-1", status: "cancelling" });
    expect(() => parseFrameOptimizeCancelReceipt({ task_id: "task-1", status: "cancelled" }, "task-1"))
      .toThrow(ApiError);
  });
});
