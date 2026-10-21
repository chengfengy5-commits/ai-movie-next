import { describe, expect, it } from "vitest";
import { ApiError } from "./errors";
import {
  parseFrameReviewCancelReceipt,
  parseFrameReviewCapabilities,
  parseFrameReviewReceipt,
  parseFrameReviewResult,
  parseFrameReviewTask,
} from "./frameReviewHttp";

describe("frame review HTTP contracts", () => {
  it("keeps every registered text provider and safe disabled reasons", () => {
    expect(parseFrameReviewCapabilities({
      operation: "frame.review",
      enabled: true,
      reason: null,
      supported_providers: ["openai", "deepseek", "custom"],
    })).toEqual({
      operation: "frame.review",
      enabled: true,
      reason: null,
      supported_providers: ["openai", "deepseek", "custom"],
    });
    expect(parseFrameReviewCapabilities({
      operation: "frame.review",
      enabled: false,
      reason: "worker_unavailable",
      supported_providers: ["openai", "deepseek", "custom"],
    }).enabled).toBe(false);
    expect(() => parseFrameReviewCapabilities({
      operation: "frame.review",
      enabled: false,
      reason: "database_password",
      supported_providers: ["openai"],
    })).toThrow(ApiError);
  });

  it("parses the registered receipt and JSON-string task result against its exact target", () => {
    expect(parseFrameReviewReceipt({
      task_id: "review-task-1",
      model_name: "Review model",
      is_system: false,
      credit_cost: 4,
    })).toEqual({
      task_id: "review-task-1",
      model_name: "Review model",
      is_system: false,
      credit_cost: 4,
    });
    const task = parseFrameReviewTask({
      id: "review-task-1",
      type: "ai-review",
      status: "completed",
      progress: 100,
      progress_message: null,
      result: JSON.stringify({
        chapter_id: "chapter-1",
        frame_index: 2,
        prompt_id: "prompt-1",
        optimized_text: "修订后的镜头",
        original_text: "原始镜头",
      }),
      credit_cost: 4,
      model_name: "Review model",
    }, "review-task-1");
    expect(parseFrameReviewResult(task, {
      chapterId: "chapter-1",
      frameIndex: 2,
      promptId: "prompt-1",
    })).toEqual({
      chapter_id: "chapter-1",
      frame_index: 2,
      prompt_id: "prompt-1",
      optimized_text: "修订后的镜头",
      original_text: "原始镜头",
    });
    expect(() => parseFrameReviewResult(task, {
      chapterId: "chapter-1",
      frameIndex: 1,
      promptId: "prompt-1",
    })).toThrow(ApiError);
    expect(() => parseFrameReviewTask({
      id: "other-task",
      type: "batch-optimize",
      status: "completed",
      result: "{}",
      credit_cost: 0,
    }, "review-task-1")).toThrow(ApiError);
  });

  it("accepts only a matching cancel acknowledgement", () => {
    expect(parseFrameReviewCancelReceipt({ task_id: "review-task-1", status: "cancelling" }, "review-task-1"))
      .toEqual({ task_id: "review-task-1", status: "cancelling" });
    expect(() => parseFrameReviewCancelReceipt({ task_id: "other-task", status: "cancelled" }, "review-task-1"))
      .toThrow(ApiError);
  });
});
