import { describe, expect, it } from "vitest";
import type { Chapter } from "../../shared/api/contracts";
import { captureFrameOriginalTextSnapshot } from "./frameOriginalTextSnapshot";

function chapter(content: unknown): Chapter {
  return {
    id: "original-text-chapter",
    series_id: "original-text-series",
    title: "原文测试",
    content: content as Chapter["content"],
    order: 1,
    created_at: "2026-10-28T00:00:00Z",
    updated_at: "2026-10-28T00:00:00Z",
    lock: null,
  };
}

describe("frame original text snapshot", () => {
  it("keeps the original string verbatim and does not use edited text as a fallback", () => {
    expect(captureFrameOriginalTextSnapshot(chapter([{ text: "草稿", original_text: "  原文\n" }]), 1))
      .toEqual({ kind: "text", value: "  原文\n" });
    expect(captureFrameOriginalTextSnapshot(chapter([{ text: "只有编辑文字" }]), 1))
      .toEqual({ kind: "missing" });
  });

  it("distinguishes an unrecognized original value from missing text", () => {
    expect(captureFrameOriginalTextSnapshot(chapter([{ text: "草稿", original_text: { value: "未知" } }]), 1))
      .toEqual({ kind: "unrecognized" });
    expect(captureFrameOriginalTextSnapshot(chapter([{ text: "草稿", original_text: null }]), 1))
      .toEqual({ kind: "unrecognized" });
    expect(captureFrameOriginalTextSnapshot(chapter(null), 1)).toEqual({ kind: "unrecognized" });
  });
});
