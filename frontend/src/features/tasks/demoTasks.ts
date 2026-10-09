import type { MyTaskRecord } from "../../shared/api/myTasks";

function task(
  id: string,
  overrides: Partial<MyTaskRecord> = {},
): MyTaskRecord {
  return {
    id,
    type: "image",
    message_id: `message-${id}`,
    status: "completed",
    result: "完成记录仅供查看。",
    request_data: "{\"prompt\":\"本地演示记录\"}",
    progress_message: "任务已完成",
    credit_cost: 3,
    progress: 100,
    created_at: "2026-10-04T08:30:00",
    updated_at: "2026-10-04T08:31:00",
    asset_type: null,
    asset_id: null,
    asset_name: null,
    chapter_title: null,
    chapter_id: null,
    frame_index: null,
    frame_count: null,
    frame_text: null,
    ...overrides,
  };
}

export const demoTaskRecords: readonly MyTaskRecord[] = [
  task("task-demo-01", { asset_type: "character", asset_id: "character-lin", asset_name: "林岚", chapter_title: "雾港来信 · 第 1 章", frame_index: 1 }),
  task("task-demo-02", { type: "batch-optimize", progress: 68, progress_message: "正在整理分镜", chapter_id: "demo-chapter-01", chapter_title: "雾港来信 · 第 1 章", frame_count: 3, request_data: "{\"chapter_id\":\"demo-chapter-01\",\"frame_count\":3}" }),
  task("task-demo-03", { status: "failed", result: "演示失败说明：输入信息不足。", credit_cost: 0, progress: 22 }),
  task("task-demo-04", { status: "queued", result: null, progress: 0, progress_message: "等待处理" }),
  task("task-demo-05", { type: "chat", status: "completed", result: null, progress: 100 }),
  task("task-demo-06", { type: "legacy_custom_task", status: "__proto__", progress: -1, credit_cost: -2, frame_index: -1 }),
  task("task-demo-07", { type: "video", status: "processing", progress: 34, progress_message: "处理中" }),
  task("task-demo-08", { status: "cancelled", result: "已有取消记录。", progress: 0 }),
  task("task-demo-09", { asset_name: null, chapter_title: null, created_at: null, updated_at: null }),
  task("task-demo-10", {
    type: "ai-review",
    chapter_id: "demo-chapter-01",
    chapter_title: "雾港来信 · 第 1 章",
    frame_index: 2,
    prompt_id: { legacy: "unknown" },
    request_data: JSON.stringify({
      chapter_id: "demo-chapter-01",
      frame_index: 1,
      prompt_id: { legacy: "unknown" },
    }),
  }),
  task("task-demo-11", { type: "legacy_custom_task", status: "mystery", request_data: "截断的旧请求文本，不会在任务卡片显示。" }),
  task("task-demo-12", { status: "failed", frame_text: null, result: "<script>本地演示失败说明</script>", progress: 150 }),
];

export function getDemoMyTaskPage(page: number): {
  total: number;
  page: number;
  page_size: 10;
  tasks: MyTaskRecord[];
} {
  const start = (page - 1) * 10;
  return {
    total: demoTaskRecords.length,
    page,
    page_size: 10,
    tasks: demoTaskRecords.slice(start, start + 10).map((item) => ({ ...item })),
  };
}
