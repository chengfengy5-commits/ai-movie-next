// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CanvasOwnedResultItem, CanvasOwnedResultTargetSnapshot } from "../../../shared/api/canvasOwnedResults";
import type { CanvasGenerationApplicationCandidate } from "../../../shared/api/canvasGenerationApplications";
import type { CanvasResultApplicationLookup } from "../../../shared/api/canvasResultApplications";
import {
  CanvasGenerationResultsDialog,
  type CanvasGenerationResultsDialogProps,
  type PendingCanvasGenerationIntent,
} from "./CanvasGenerationResultsDialog";

const frameTarget = {
  kind: "frame_preview",
  chapter_id: "chapter-a",
  identity_mode: "strict-no-id-v1",
  frame_index: 0,
  expected_chapter_digest: "e".repeat(64),
} as const;

const targetSnapshot: CanvasOwnedResultTargetSnapshot = {
  kind: "canvas_frame_preview",
  chapter_id: "chapter-a",
  series_id: "series-a",
  stable_storyboard_asset_id: null,
  asset_type: null,
  asset_id: null,
  compat_frame_index: 0,
  expected_text_digest: null,
  media_identity: null,
  content_digest: null,
  notes_revision: null,
  full_frame_digest: "a".repeat(64),
  full_chapter_digest: "e".repeat(64),
  expected_asset_image_url: null,
  same_series_target_digest: null,
};

function result(
  eligibility: CanvasOwnedResultItem["eligibility"],
  resultRef: string,
): CanvasOwnedResultItem {
  return {
    task_id: "task-a",
    result_ref: resultRef,
    operation: "image.single",
    completed_at: "2026-10-25T00:00:00Z",
    expected_target: eligibility === "unready" ? null : targetSnapshot,
    application_key: eligibility === "unready" ? null : "c".repeat(64),
    eligibility,
    application: eligibility === "already_applied"
      ? {
        application_ref: "application-a",
        target_revision: "d".repeat(64),
        committed_at: "2026-10-25T01:00:00+00:00",
      }
      : null,
  };
}

const candidate: CanvasGenerationApplicationCandidate = {
  task_id: "task-a",
  result_ref: "ready-result",
  expected_target: targetSnapshot,
  application_key: "c".repeat(64),
};

function pendingIntent(
  state: PendingCanvasGenerationIntent["state"],
  lookup: CanvasResultApplicationLookup | null = null,
): PendingCanvasGenerationIntent {
  return {
    destination: frameTarget,
    candidate,
    sourceVersion: "b".repeat(64),
    state,
    lookup,
  };
}

function renderDialog(overrides: Partial<CanvasGenerationResultsDialogProps> = {}) {
  const props: CanvasGenerationResultsDialogProps = {
    targetLabel: "第 1 帧",
    items: [result("ready", "ready-result")],
    nextCursor: null,
    loading: false,
    busy: false,
    error: null,
    message: null,
    pendingIntent: null,
    historyLookups: new Map(),
    lockRequired: false,
    lockUnavailable: false,
    lockPending: false,
    lockMessage: null,
    confirmedProof: null,
    onClose: vi.fn(),
    onRefresh: vi.fn(),
    onLoadMore: vi.fn(),
    onApply: vi.fn(),
    onVerify: vi.fn(),
    onLookupPendingIntent: vi.fn(),
    onRetryPendingIntent: vi.fn(),
    onAcquireLock: vi.fn(),
    ...overrides,
  };
  return { ...render(<CanvasGenerationResultsDialog {...props} />), props };
}

afterEach(() => cleanup());

describe("CanvasGenerationResultsDialog", () => {
  it("shows apply only for ready results and history verification for already-applied results", () => {
    const items = [
      result("ready", "ready-result"),
      result("already_applied", "applied-result"),
      result("stale", "stale-result"),
      result("target_deleted", "deleted-result"),
      result("unready", "unready-result"),
    ];
    const { props } = renderDialog({ items });

    expect(screen.getAllByRole("button", { name: "应用到当前目标" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "核对应用记录" })).toHaveLength(1);
    expect(screen.getByText("此前已应用")).toBeTruthy();
    expect(screen.getByText("原始目标版本已变化")).toBeTruthy();
    expect(screen.getByText("原始目标已删除")).toBeTruthy();
    expect(screen.getByText("记录尚未就绪")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "应用到当前目标" }));
    expect(props.onApply).toHaveBeenCalledWith(items[0]);
    fireEvent.click(screen.getByRole("button", { name: "核对应用记录" }));
    expect(props.onVerify).toHaveBeenCalledWith(items[1]);
  });

  it("keeps page loading manual and exposes an explicit lock action without enabling apply", () => {
    const { props } = renderDialog({ nextCursor: "YWJj", lockRequired: true });

    expect(screen.getByRole("button", { name: "加载下一页" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "取得编辑锁" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "应用到当前目标" })).toBeDisabled();
    expect(props.onLoadMore).not.toHaveBeenCalled();
    expect(props.onAcquireLock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "加载下一页" }));
    fireEvent.click(screen.getByRole("button", { name: "取得编辑锁" }));
    expect(props.onLoadMore).toHaveBeenCalledOnce();
    expect(props.onAcquireLock).toHaveBeenCalledOnce();
  });

  it("keeps unknown and not-applied intents explicit and never retries on a readback alone", () => {
    const lookup: CanvasResultApplicationLookup = {
      task_id: "task-a",
      result_ref: "ready-result",
      status: "not_applied",
      application: null,
    };
    const unknown = renderDialog({ pendingIntent: pendingIntent("unknown") });

    expect(screen.getByRole("button", { name: "继续核对" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "重新提交原操作" })).toBeNull();
    expect(screen.getByRole("button", { name: "应用到当前目标" })).toBeDisabled();
    const pendingAlert = screen.getByRole("alert");
    expect(within(pendingAlert).getByText("任务编号：task-a").closest("details")?.open).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "继续核对" }));
    expect(unknown.props.onLookupPendingIntent).toHaveBeenCalledOnce();
    unknown.unmount();

    const notApplied = renderDialog({ pendingIntent: pendingIntent("not_applied", lookup) });
    expect(within(screen.getByRole("alert")).getAllByText(/暂未查到应用记录，之前请求仍可能稍后完成/)).toHaveLength(2);
    expect(screen.getByRole("button", { name: "重新提交原操作" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "应用到当前目标" })).toBeDisabled();
    expect(notApplied.props.onRetryPendingIntent).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重新提交原操作" }));
    expect(notApplied.props.onRetryPendingIntent).toHaveBeenCalledOnce();
  });

  it("labels application proofs as historical and keeps current-image state separate", () => {
    const lookup: CanvasResultApplicationLookup = {
      task_id: "task-a",
      result_ref: "applied-result",
      status: "applied",
      application: {
        application_ref: "verified-application",
        target_revision: "f".repeat(64),
        committed_at: "2026-10-25T02:00:00+00:00",
      },
    };
    renderDialog({
      items: [result("already_applied", "applied-result")],
      historyLookups: new Map([["applied-result", lookup]]),
      confirmedProof: {
        resultRef: "applied-result",
        status: "already_applied",
        applicationRef: "verified-application",
        targetRevision: "f".repeat(64),
        committedAt: "2026-10-25T02:00:00+00:00",
      },
    });

    expect(screen.getAllByRole("status").some((status) => status.textContent?.includes("不代表当前图像仍未被后续手工修改"))).toBe(true);
    const applicationReferences = screen.getAllByText("应用记录编号：verified-application");
    expect(applicationReferences).toHaveLength(2);
    expect(applicationReferences.every((reference) => reference.closest("details")?.open === false)).toBe(true);
    expect(screen.getByRole("button", { name: "核对应用记录" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "应用到当前目标" })).toBeNull();
  });
});
