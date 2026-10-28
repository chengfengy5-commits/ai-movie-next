import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { MyTaskRecord } from "../../shared/api/myTasks";
import { ApiError } from "../../shared/api/errors";
import { parseTaskReceipt, parseTaskRequestRecord } from "../../shared/api/taskDetails";
import type { WorkspaceServices } from "../../shared/api/services";
import { TaskDetailDialog } from "./TaskDetailDialog";
import {
  createTaskControlPendingScope,
  hasTaskControlPendingMarker,
  writeTaskControlPendingMarker,
} from "./taskControlPending";

const selectedTask: MyTaskRecord = {
  id: "task-control-1",
  type: "ai-review",
  message_id: "message-1",
  status: "processing",
  result: null,
  request_data: "{}",
  progress_message: "仍在处理",
  credit_cost: 5,
  progress: 40,
  created_at: "2026-10-28T00:00:00Z",
  updated_at: "2026-10-28T00:01:00Z",
  asset_type: null,
  asset_id: null,
  asset_name: null,
  chapter_title: null,
  chapter_id: null,
  frame_index: null,
  frame_count: null,
  frame_text: null,
};

function receipt(status: string, result: unknown = null) {
  return parseTaskReceipt({
    id: selectedTask.id,
    type: selectedTask.type,
    message_id: selectedTask.message_id,
    status,
    result,
    billing_status: "unknown",
    quoted_amount: 5,
    progress: status === "completed" ? 100 : 40,
    progress_message: status === "completed" ? "任务记录已完成" : "仍在处理",
    external_task_id: null,
    external_provider: null,
    created_at: selectedTask.created_at,
    updated_at: selectedTask.updated_at,
  }, selectedTask.id);
}

function services(overrides: Partial<WorkspaceServices> = {}): WorkspaceServices {
  return {
    mode: "api",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    restore: async () => null,
    login: async () => { throw new Error("Unexpected login request."); },
    listSeries: async () => [],
    listMyTeams: async () => [],
    listChapters: async () => [],
    listStoryboardAssets: async () => [],
    listCharacters: async () => [],
    listScenes: async () => [],
    listProps: async () => [],
    getPersonalProductionNotes: async () => { throw new Error("Unexpected notes request."); },
    getPersonalRoughCut: async () => { throw new Error("Unexpected rough-cut request."); },
    listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    getTaskReceipt: async () => receipt("processing"),
    getTaskRequest: async () => parseTaskRequestRecord({
      id: selectedTask.id,
      type: selectedTask.type,
      status: "processing",
      model_name: null,
      credit_cost: 5,
      external_task_id: null,
      external_provider: null,
      created_at: selectedTask.created_at,
      request: { chapter_id: "chapter-1" },
    }, selectedTask.id),
    logout: () => undefined,
    ...overrides,
  };
}

function controls(status: string, allowed: boolean, reason: "available" | "already_requested" | "terminal") {
  return { taskId: selectedTask.id, status, cancel: { allowed, reason } };
}

function detail(servicesValue: WorkspaceServices, onClose: () => void) {
  return (
    <TaskDetailDialog
      onCancelAttemptMayHaveReachedServer={vi.fn()}
      onClose={onClose}
      onUnauthorized={vi.fn()}
      openGeneration={1}
      services={servicesValue}
      sessionGeneration={1}
      task={selectedTask}
      userId="user-1"
    />
  );
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  window.localStorage.clear();
});

describe("TaskDetailDialog task controls", () => {
  it("keeps demo-mode task records read-only", async () => {
    const getTaskControls = vi.fn(async () => controls("processing", true, "available"));
    const cancelTask = vi.fn(async () => ({ taskId: selectedTask.id, status: "cancelling" }));
    const api = services({ mode: "demo", apiBaseUrl: null, getTaskControls, cancelTask });

    render(detail(api, vi.fn()));

    expect(await screen.findByText(/演示环境只提供本地只读记录/u)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "请求取消" })).not.toBeInTheDocument();
    expect(getTaskControls).not.toHaveBeenCalled();
    expect(cancelTask).not.toHaveBeenCalled();
  });

  it("pauses while hidden, resumes a pending read, and keeps a paused loop stopped", async () => {
    const descriptor = Object.getOwnPropertyDescriptor(document, "hidden");
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    const scope = createTaskControlPendingScope("user-1", "http://127.0.0.1:4175/api", selectedTask.id);
    if (scope === null) throw new Error("Expected a valid task marker scope.");
    writeTaskControlPendingMarker(scope);
    const getTaskControls = vi.fn()
      .mockResolvedValueOnce(controls("processing", false, "already_requested"))
      .mockRejectedValueOnce(new ApiError("http", "Controls unavailable", 403));

    try {
      render(detail(services({ getTaskControls }), vi.fn()));
      await screen.findByText("取消能力来源");
      expect(getTaskControls).toHaveBeenCalledTimes(1);

      await new Promise((resolve) => window.setTimeout(resolve, 3_100));
      expect(getTaskControls).toHaveBeenCalledTimes(1);

      Object.defineProperty(document, "hidden", { configurable: true, value: false });
      document.dispatchEvent(new Event("visibilitychange"));
      await new Promise((resolve) => window.setTimeout(resolve, 3_100));
      await screen.findByText(/自动核对已停止/u);
      expect(getTaskControls).toHaveBeenCalledTimes(2);

      Object.defineProperty(document, "hidden", { configurable: true, value: true });
      document.dispatchEvent(new Event("visibilitychange"));
      Object.defineProperty(document, "hidden", { configurable: true, value: false });
      document.dispatchEvent(new Event("visibilitychange"));
      await new Promise((resolve) => window.setTimeout(resolve, 3_100));
      expect(getTaskControls).toHaveBeenCalledTimes(2);
    } finally {
      if (descriptor === undefined) Reflect.deleteProperty(document, "hidden");
      else Object.defineProperty(document, "hidden", descriptor);
    }
  }, 12_000);

  it("observes reconciling task states without resubmitting", async () => {
    const scope = createTaskControlPendingScope("user-1", "http://127.0.0.1:4175/api", selectedTask.id);
    if (scope === null) throw new Error("Expected a valid task marker scope.");
    writeTaskControlPendingMarker(scope);
    const getTaskControls = vi.fn(async () => controls("reconciling", false, "already_requested"));

    render(detail(services({ getTaskControls }), vi.fn()));
    await screen.findByText("取消能力来源");
    await new Promise((resolve) => window.setTimeout(resolve, 3_100));

    expect(getTaskControls).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "核对任务与取消状态" })).toBeInTheDocument();
  }, 5_000);

  it("clears an unknown marker after an independently observed terminal task status", async () => {
    const scope = createTaskControlPendingScope("user-1", "http://127.0.0.1:4175/api", selectedTask.id);
    if (scope === null) throw new Error("Expected a valid task marker scope.");
    writeTaskControlPendingMarker(scope);
    const getTaskControls = vi.fn(async () => controls("failed", false, "already_requested"));

    render(detail(services({ getTaskControls }), vi.fn()));

    await screen.findByText("failed");
    expect(screen.queryByText(/尚未解决的取消请求记录/u)).not.toBeInTheDocument();
    expect(hasTaskControlPendingMarker(scope)).toBe(false);
    expect(screen.getByText(/服务器当前任务记录为终态/u)).toBeInTheDocument();
  });

  it("requires fresh permission, sends once, and trusts the independent GET over the cancel receipt", async () => {
    const getTaskControls = vi.fn()
      .mockResolvedValueOnce(controls("processing", true, "available"))
      .mockResolvedValueOnce(controls("processing", true, "available"))
      .mockResolvedValueOnce(controls("completed", false, "terminal"));
    const cancelTask = vi.fn(async () => ({ taskId: selectedTask.id, status: "cancelling" }));
    const getTaskReceipt = vi.fn()
      .mockResolvedValueOnce(receipt("processing"))
      .mockResolvedValueOnce(receipt("completed", { partial: ["已有部分结果"] }));
    const api = services({ getTaskControls, cancelTask, getTaskReceipt });
    const onClose = vi.fn();

    render(detail(api, onClose));
    await screen.findByText("取消能力来源");
    fireEvent.click(screen.getByRole("button", { name: "请求取消" }));
    fireEvent.click(screen.getByRole("button", { name: "确认并请求取消" }));

    await within(screen.getByRole("region", { name: "任务操作与状态核对" })).findByText("completed");
    expect(cancelTask).toHaveBeenCalledTimes(1);
    expect(getTaskControls).toHaveBeenCalledTimes(3);
    expect(getTaskReceipt).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/服务端任务记录当前为终态/u)).toBeInTheDocument();
    expect(screen.getByText(/部分结果/u)).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "任务详情" })).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("reads the task receipt even when post-cancel controls refresh fails", async () => {
    const getTaskControls = vi.fn()
      .mockResolvedValueOnce(controls("processing", true, "available"))
      .mockResolvedValueOnce(controls("processing", true, "available"))
      .mockRejectedValueOnce(new ApiError("http", "Controls unavailable", 503));
    const cancelTask = vi.fn(async () => ({ taskId: selectedTask.id, status: "cancelling" }));
    const getTaskReceipt = vi.fn()
      .mockResolvedValueOnce(receipt("processing"))
      .mockResolvedValueOnce(receipt("processing"));

    render(detail(services({ getTaskControls, cancelTask, getTaskReceipt }), vi.fn()));
    await screen.findByText("取消能力来源");
    fireEvent.click(screen.getByRole("button", { name: "请求取消" }));
    fireEvent.click(screen.getByRole("button", { name: "确认并请求取消" }));

    await screen.findByText(/取消接口已返回状态记录/u);
    await waitFor(() => expect(getTaskReceipt).toHaveBeenCalledTimes(2));
    expect(getTaskControls).toHaveBeenCalledTimes(3);
    expect(cancelTask).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert")).toHaveTextContent("Controls unavailable");
  });

  it("keeps an unknown cancel marker across close and reopen and only re-reads", async () => {
    const getTaskControls = vi.fn(async () => controls("processing", true, "available"));
    const cancelTask = vi.fn(async () => { throw new ApiError("network", "network lost"); });
    const api = services({ getTaskControls, cancelTask });
    function Harness() {
      const [open, setOpen] = useState(true);
      return open
        ? detail(api, () => setOpen(false))
        : <button onClick={() => setOpen(true)} type="button">重新打开详情</button>;
    }

    render(<Harness />);
    await screen.findByText("取消能力来源");
    fireEvent.click(screen.getByRole("button", { name: "请求取消" }));
    fireEvent.click(screen.getByRole("button", { name: "确认并请求取消" }));
    await screen.findByText(/取消请求结果尚未确认/u);
    expect(cancelTask).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "关闭任务详情" }));

    fireEvent.click(screen.getByRole("button", { name: "重新打开详情" }));
    await screen.findByText(/尚未解决的取消请求记录/u);
    expect(screen.queryByRole("button", { name: "请求取消" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "核对任务与取消状态" }));
    await waitFor(() => expect(getTaskControls).toHaveBeenCalledTimes(5));
    expect(cancelTask).toHaveBeenCalledTimes(1);
  });

  it("ignores a late 401 after the detail session generation changes", async () => {
    let rejectOldRead!: (cause: unknown) => void;
    let resolveNewRead!: (value: ReturnType<typeof controls>) => void;
    const oldRead = new Promise<ReturnType<typeof controls>>((_resolve, reject) => { rejectOldRead = reject; });
    const getTaskControls = vi.fn()
      .mockReturnValueOnce(oldRead)
      .mockReturnValueOnce(new Promise((resolve) => { resolveNewRead = resolve; }));
    const api = services({ getTaskControls });
    const oldUnauthorized = vi.fn();
    const newUnauthorized = vi.fn();
    const onClose = vi.fn();
    const view = render(
      <TaskDetailDialog
        onCancelAttemptMayHaveReachedServer={vi.fn()}
        onClose={onClose}
        onUnauthorized={oldUnauthorized}
        openGeneration={1}
        services={api}
        sessionGeneration={1}
        task={selectedTask}
        userId="user-1"
      />,
    );
    view.rerender(
      <TaskDetailDialog
        onCancelAttemptMayHaveReachedServer={vi.fn()}
        onClose={onClose}
        onUnauthorized={newUnauthorized}
        openGeneration={1}
        services={api}
        sessionGeneration={2}
        task={selectedTask}
        userId="user-1"
      />,
    );
    await waitFor(() => expect(getTaskControls).toHaveBeenCalledTimes(2));
    await act(async () => { resolveNewRead(controls("processing", true, "available")); });
    await screen.findByText("取消能力来源");
    await act(async () => { rejectOldRead(new ApiError("http", "登录失效", 401)); });
    expect(oldUnauthorized).not.toHaveBeenCalled();
    expect(newUnauthorized).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "任务操作与状态核对" })).getByText("processing")).toBeInTheDocument();
  });
});
