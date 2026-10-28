import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ContinuousChapterOperationEnvelope } from "./continuousChapterOperationJournal";
import { ContinuousChapterRecoveryPanel } from "./ContinuousChapterRecoveryPanel";

const pendingJournal = {
  schema_version: 1,
  integrity_sha256: "0".repeat(64),
  origin: {
    actor_id: "author-1",
    api_base_url: "https://api.example.test/v1",
    series_id: "series-1",
    chapter_id: "chapter-1",
    editor_session_id: "00000000-0000-4000-8000-000000000001",
    document_id: "00000000-0000-4000-8000-000000000002",
    auth_session_generation: 4,
  },
  created_at: "2026-10-28T00:00:00Z",
  phase: "dispatch_possible",
  snapshot: {
    chapter: { id: "chapter-1", title: "第一章" },
    frames: [{
      draftId: "source:0",
      sourceIndex: 0,
      storyboardId: "shot-1",
      originalFrame: { text: "旧正文", storyboard: ["shot-1"] },
      text: "保留的原草稿",
      characterIds: [],
      sceneIds: [],
      propIds: [],
    }],
    expected_storyboards: [],
    lock_identity: null,
  },
  selected_draft_id: "source:0",
  operation: {
    operation_id: "00000000-0000-4000-8000-000000000003",
    kind: "save-chapter",
    target_draft_id: null,
    draft_frame_ids: ["source:0"],
    request_body_json: "{}",
    request_body_sha256: "1".repeat(64),
    source_snapshot_digest: "2".repeat(64),
    target_manifest_digest: "3".repeat(64),
    route: "/chapters/chapter-1/continuous-edit/operations/00000000-0000-4000-8000-000000000003",
  },
  observation: { message: null, receipt: null, result: null, target_status: "unchecked" },
} as unknown as ContinuousChapterOperationEnvelope;

describe("ContinuousChapterRecoveryPanel", () => {
  it("shows the saved draft and only offers original-operation lookup while the result is unknown", () => {
    const onVerify = vi.fn();
    const onResolve = vi.fn();
    render(
      <ContinuousChapterRecoveryPanel
        chapterTitle="第一章"
        status="ready"
        journal={pendingJournal}
        message={null}
        busy={false}
        onRetryRead={vi.fn()}
        onVerify={onVerify}
        onResolve={onResolve}
        onContinueDraft={vi.fn()}
        onDiscardUndispatched={vi.fn()}
      />,
    );

    expect(screen.getByRole("region", { name: "连续编辑操作恢复" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "原连续编辑草稿" })).toHaveTextContent("保留的原草稿");
    expect(screen.getByText("请求可能已发出，等待核对")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新读取原操作" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "我已审阅，解除本章写入冻结" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重新读取原操作" }));
    expect(onVerify).toHaveBeenCalledOnce();
    expect(onResolve).not.toHaveBeenCalled();
  });
});
