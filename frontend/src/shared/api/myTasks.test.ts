import { describe, expect, it } from "vitest";
import { InvalidResponseError } from "./contracts";
import {
  MY_TASK_PAGE_SIZE,
  parseMyTaskPage,
  parseMyTaskRecord,
  projectMyTask,
  type MyTaskRecord,
} from "./myTasks";

function task(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "task-1",
    type: "image",
    message_id: "message-1",
    status: "processing",
    result: null,
    request_data: "{\"prompt\":\"截断片段",
    progress_message: "正在生成",
    credit_cost: -2,
    progress: 42,
    created_at: "2026-10-05T00:00:00",
    updated_at: "2026-10-05T08:00:00+08:00",
    asset_type: "character",
    asset_id: "asset-1",
    asset_name: "林岚",
    chapter_title: "雾港来信 · 第 1 章",
    chapter_id: { legacy: true },
    frame_index: -1,
    frame_count: ["legacy", 3],
    frame_text: null,
    ...overrides,
  };
}

function page(tasks: unknown[], overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return { total: tasks.length, page: 1, page_size: MY_TASK_PAGE_SIZE, tasks, ...overrides };
}

describe("my task response contracts", () => {
  it("requires own fields and distinguishes required nullable values from missing fields", () => {
    expect(() => parseMyTaskRecord(task({ result: undefined }))).toThrow(InvalidResponseError);
    expect(() => parseMyTaskRecord(task({ result: null }))).not.toThrow();

    const inherited = Object.assign(Object.create({ id: "inherited-id" }) as Record<string, unknown>, task());
    delete inherited.id;
    expect(() => parseMyTaskRecord(inherited)).toThrow(/id/);
  });

  it("accepts dynamic historical fields and signed integers without reordering rows", () => {
    const first = parseMyTaskRecord(task({
      chapter_id: { legacy: ["value"] },
      frame_count: ["old", 2],
      prompt_id: { unknown: true },
      credit_cost: -4,
      progress: -3,
      frame_index: -1,
      request_data: "截断的旧请求片段🙂".repeat(40),
    }));
    const second = parseMyTaskRecord(task({ id: "task-2", type: "unknown-type" }));
    const parsed = parseMyTaskPage(page([task(), task({ id: "task-2" })]), 1);

    expect(first.chapter_id).toEqual({ legacy: ["value"] });
    expect(first.frame_count).toEqual(["old", 2]);
    expect(first.prompt_id).toEqual({ unknown: true });
    expect(first.credit_cost).toBe(-4);
    expect(first.progress).toBe(-3);
    expect(first.frame_index).toBe(-1);
    expect(first.request_data).toContain("🙂");
    expect(second.type).toBe("unknown-type");
    expect(parsed.tasks.map((item) => item.id)).toEqual(["task-1", "task-2"]);
  });

  it("rejects page echo, unsafe integers, oversized pages, and duplicate identities", () => {
    expect(() => parseMyTaskPage(page([]), 2)).toThrow(/页码/);
    expect(() => parseMyTaskPage(page([], { page_size: 20 }), 1)).toThrow(/页大小/);
    expect(() => parseMyTaskPage(page([], { total: Number.MAX_SAFE_INTEGER + 1 }), 1)).toThrow(/total/);
    expect(() => parseMyTaskPage(page([task(), task()]), 1)).toThrow(/重复/);
    expect(() => parseMyTaskPage(page(Array.from({ length: 11 }, (_, index) => task({ id: `task-${index}` }))), 1))
      .toThrow(/内容格式/);
  });

  it("keeps a valid empty page distinct and preserves unknown dynamic JSON", () => {
    expect(parseMyTaskPage(page([]), 1)).toEqual({ total: 0, page: 1, page_size: 10, tasks: [] });
    const parsed = parseMyTaskRecord(task({ chapter_id: null, frame_count: "old", prompt_id: [] }));
    expect(parsed.chapter_id).toBeNull();
    expect(parsed.frame_count).toBe("old");
    expect(parsed.prompt_id).toEqual([]);
  });

  it("interprets timestamps without zones as UTC and honors explicit offsets", () => {
    const naive = parseMyTaskRecord(task({ created_at: "2026-10-05T00:00:00" }));
    const offset = parseMyTaskRecord(task({ created_at: "2026-10-05T08:00:00+08:00" }));
    const naivePresentation = projectMyTask(naive, "Asia/Shanghai");
    const offsetPresentation = projectMyTask(offset, "Asia/Shanghai");

    expect(naivePresentation.createdAt).toBe(offsetPresentation.createdAt);
    expect(naivePresentation.createdAt).toContain("08:00");
    expect(projectMyTask(parseMyTaskRecord(task({ created_at: null }))).createdAt).toBe("未提供");
  });

  it("uses safe labels for prototype-named values and projects only conservative plain text", () => {
    const raw = parseMyTaskRecord(task({
      type: "__proto__",
      status: "constructor",
      result: "https://example.invalid/private-output",
      asset_type: "__proto__",
      asset_name: "<b>旧资产</b>",
      chapter_title: "旧章节",
      frame_index: 2,
      frame_count: 0,
      frame_text: "不投影的分镜正文",
      progress: 101,
    }));
    const projected = projectMyTask(raw);
    const typed: MyTaskRecord = raw;

    expect(projected.type).toBe("__proto__");
    expect(projected.status).toBe("状态未识别");
    expect(projected.progress).toBeNull();
    expect(projected.assetType).toBe("__proto__");
    expect(projected.assetName).toBe("<b>旧资产</b>");
    expect(projected.chapterTitle).toBe("旧章节");
    expect(projected.framePosition).toBe("分镜 2");
    expect(projected.frameCount).toBe("共 0 帧");
    expect("frameText" in projected).toBe(false);
    expect(projected.result).toBe("已有结果记录");
    expect(typed.request_data).toContain("截断");
  });
});
