import { describe, expect, it } from "vitest";
import { ApiError } from "./errors";
import {
  parseTaskCancelAcknowledgement,
  parseTaskControls,
} from "./taskControls";

describe("task controls response parsing", () => {
  it("accepts an exact current task controls snapshot", () => {
    expect(parseTaskControls({
      task_id: "task-1",
      status: "processing",
      cancel: { allowed: true, reason: "available" },
    }, "task-1")).toEqual({
      taskId: "task-1",
      status: "processing",
      cancel: { allowed: true, reason: "available" },
    });
  });

  it.each([
    { task_id: "other-task", status: "processing", cancel: { allowed: true, reason: "available" } },
    { task_id: "task-1", status: "processing", cancel: { allowed: true, reason: "terminal" } },
    { task_id: "task-1", status: "processing", cancel: { allowed: false, reason: "available" } },
    { task_id: "task-1", status: "processing", cancel: { allowed: false, reason: "not-a-reason" } },
    { task_id: "task-1", status: "processing", cancel: { reason: "available" } },
  ])("fails closed for malformed or mismatched controls: %#", (value) => {
    expect(() => parseTaskControls(value, "task-1")).toThrow(ApiError);
  });

  it("treats the cancel response as an identity-checked compatibility receipt", () => {
    expect(parseTaskCancelAcknowledgement({ task_id: "task-1", status: "completed" }, "task-1"))
      .toEqual({ taskId: "task-1", status: "completed" });
    expect(() => parseTaskCancelAcknowledgement({ task_id: "other-task", status: "cancelling" }, "task-1"))
      .toThrow(/其他任务/u);
  });
});
