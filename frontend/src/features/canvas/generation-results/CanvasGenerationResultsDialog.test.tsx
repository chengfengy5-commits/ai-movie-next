// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CanvasOwnedImageError, type CanvasOwnedImageRun, type CanvasOwnedImageSelector } from "../../../shared/api/canvasOwnedImage";
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

const originalCreateObjectUrl = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const originalRevokeObjectUrl = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
const previewServiceIdentity = {};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => { resolve = complete; });
  return { promise, resolve };
}

function restoreUrlMethod(name: "createObjectURL" | "revokeObjectURL", descriptor: PropertyDescriptor | undefined) {
  if (descriptor === undefined) Reflect.deleteProperty(URL, name);
  else Object.defineProperty(URL, name, descriptor);
}

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
    imagePreviewAvailable: true,
    imagePreviewScopeKey: "scope-a",
    imagePreviewServiceIdentity: previewServiceIdentity,
    onClose: vi.fn(),
    onRefresh: vi.fn(),
    onLoadMore: vi.fn(),
    onApply: vi.fn(),
    onVerify: vi.fn(),
    onLookupPendingIntent: vi.fn(),
    onRetryPendingIntent: vi.fn(),
    onAcquireLock: vi.fn(),
    onBeginImagePreview: vi.fn(() => ({
      isCurrent: () => true,
      read: vi.fn(async () => new Blob(["preview"], { type: "image/png" })),
    }) satisfies CanvasOwnedImageRun),
    ...overrides,
  };
  return { ...render(<CanvasGenerationResultsDialog {...props} />), props };
}

afterEach(() => {
  cleanup();
  restoreUrlMethod("createObjectURL", originalCreateObjectUrl);
  restoreUrlMethod("revokeObjectURL", originalRevokeObjectUrl);
});

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

  it("allows explicit previews for stale history and disables image reads for unready records", () => {
    const stale = result("stale", "stale-result");
    const deleted = result("target_deleted", "deleted-result");
    const unready = result("unready", "unready-result");
    const onBeginImagePreview = vi.fn(() => ({
      isCurrent: () => true,
      read: vi.fn(async () => new Blob(["preview"], { type: "image/png" })),
    }) satisfies CanvasOwnedImageRun);
    const queries = renderDialog({ items: [stale, deleted, unready], onBeginImagePreview }).container;
    const articles = within(queries).getAllByRole("article");

    expect(within(articles[0]!).getByRole("button", { name: "查看图片" })).toBeEnabled();
    expect(within(articles[1]!).getByRole("button", { name: "查看图片" })).toBeEnabled();
    expect(within(articles[2]!).getByRole("button", { name: "查看图片" })).toBeDisabled();
    expect(within(articles[2]!).getByText("记录尚未就绪，暂时不能读取图片。")).toBeTruthy();
    expect(onBeginImagePreview).not.toHaveBeenCalled();
  });

  it("cancels the previous read before switching previews and ignores its late blob", async () => {
    const urls = ["blob:canvas-result-1", "blob:canvas-result-2"];
    let nextUrl = 0;
    const createObjectURL = vi.fn((_blob: Blob) => urls[nextUrl++]!);
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    const reads: Array<{ pending: ReturnType<typeof deferred<Blob>>; signal: AbortSignal }> = [];
    const onBeginImagePreview = vi.fn(() => ({
      isCurrent: () => true,
      read: vi.fn((_selector: CanvasOwnedImageSelector, signal: AbortSignal) => {
        const pending = deferred<Blob>();
        reads.push({ pending, signal });
        return pending.promise;
      }),
    }) satisfies CanvasOwnedImageRun);
    const items = [result("stale", "stale-result"), result("already_applied", "applied-result")];
    renderDialog({ items, onBeginImagePreview });
    const articles = within(screen.getByRole("dialog", { name: "生成图片记录" })).getAllByRole("article");

    fireEvent.click(within(articles[0]!).getByRole("button", { name: "查看图片" }));
    expect(reads).toHaveLength(1);
    await act(async () => { reads[0]!.pending.resolve(new Blob(["first"], { type: "image/png" })); });
    expect(await screen.findByRole("img", { name: "生成结果图片预览" })).toHaveAttribute("src", urls[0]);

    fireEvent.click(within(articles[1]!).getByRole("button", { name: "查看图片" }));
    expect(revokeObjectURL.mock.calls).toEqual([[urls[0]]]);
    expect(reads).toHaveLength(2);
    fireEvent.click(within(articles[0]!).getByRole("button", { name: "查看图片" }));
    expect(reads[1]!.signal.aborted).toBe(true);
    expect(reads).toHaveLength(3);

    await act(async () => { reads[1]!.pending.resolve(new Blob(["late"], { type: "image/png" })); });
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    await act(async () => { reads[2]!.pending.resolve(new Blob(["third"], { type: "image/png" })); });
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("img", { name: "生成结果图片预览" })).toHaveAttribute("src", urls[1]);

    cleanup();
    expect(revokeObjectURL.mock.calls).toEqual([[urls[0]], [urls[1]]]);
  });

  it("releases a failed image URL and retries only after an explicit action", async () => {
    const urls = ["blob:broken-canvas-image", "blob:retried-canvas-image"];
    let nextUrl = 0;
    const createObjectURL = vi.fn(() => urls[nextUrl++]!);
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    const onBeginImagePreview = vi.fn(() => ({
      isCurrent: () => true,
      read: vi.fn(async () => new Blob(["image"], { type: "image/png" })),
    }) satisfies CanvasOwnedImageRun);
    renderDialog({ onBeginImagePreview });

    fireEvent.click(screen.getByRole("button", { name: "查看图片" }));
    const brokenImage = await screen.findByRole("img", { name: "生成结果图片预览" });
    expect(brokenImage).toHaveAttribute("src", urls[0]);
    fireEvent.error(brokenImage);
    expect(await screen.findByText("图片无法解码或显示，请点击“重试查看图片”。")).toBeTruthy();
    expect(revokeObjectURL.mock.calls).toEqual([[urls[0]]]);
    expect(onBeginImagePreview).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByRole("button", { name: "重试查看图片" }));
    expect(await screen.findByRole("img", { name: "生成结果图片预览" })).toHaveAttribute("src", urls[1]);
    expect(onBeginImagePreview).toHaveBeenCalledTimes(2);
    expect(createObjectURL).toHaveBeenCalledTimes(2);
  });

  it("reports object URL creation failures and retries only after an explicit action", async () => {
    const createObjectURL = vi.fn()
      .mockImplementationOnce(() => { throw new Error("object URL unavailable"); })
      .mockReturnValue("blob:created-after-retry");
    const read = vi.fn(async () => new Blob(["image"], { type: "image/png" }));
    const onBeginImagePreview = vi.fn(() => ({
      isCurrent: () => true,
      read,
    }) satisfies CanvasOwnedImageRun);
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    renderDialog({ onBeginImagePreview });

    fireEvent.click(screen.getByRole("button", { name: "查看图片" }));
    expect(await screen.findByText("图片无法读取或显示，请点击“重试查看图片”。")).toBeTruthy();
    expect(read).toHaveBeenCalledOnce();
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(screen.queryByRole("img", { name: "生成结果图片预览" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "重试查看图片" }));
    expect(await screen.findByRole("img", { name: "生成结果图片预览" })).toHaveAttribute("src", "blob:created-after-retry");
    expect(read).toHaveBeenCalledTimes(2);
    expect(createObjectURL).toHaveBeenCalledTimes(2);
  });

  it("shows a safe alert when beginning an owned image read throws a not-sent error", async () => {
    const onBeginImagePreview = vi.fn(() => {
      throw new CanvasOwnedImageError("not-sent", "private session detail");
    });
    renderDialog({ onBeginImagePreview });

    fireEvent.click(screen.getByRole("button", { name: "查看图片" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("登录或画布状态已变化，图片未显示。请重新打开记录后重试。");
    expect(screen.queryByText("private session detail")).toBeNull();
    expect(onBeginImagePreview).toHaveBeenCalledOnce();
  });

  it("shows a safe alert when the current dialog cannot create an image read run", async () => {
    const onBeginImagePreview = vi.fn(() => null);
    renderDialog({ onBeginImagePreview });

    fireEvent.click(screen.getByRole("button", { name: "查看图片" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("登录或画布状态已变化，图片未显示。请重新打开记录后重试。");
    expect(onBeginImagePreview).toHaveBeenCalledOnce();
    expect(screen.queryByRole("img", { name: "生成结果图片预览" })).toBeNull();
  });
});
