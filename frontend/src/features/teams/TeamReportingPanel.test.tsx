import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createDemoServices, type WorkspaceServices } from "../../shared/api/services";
import { ApiError } from "../../shared/api/errors";
import type {
  TeamMemberTasksPage,
  TeamModelUsageResponse,
  TeamReportingServicePort,
  TeamSeriesUsageResponse,
  TeamUsageExport,
  TeamUsageResponse,
} from "../../shared/api/teamReporting";
import type { TeamDetail } from "../../shared/api/teamManagement";
import { TeamReportingPanel } from "./TeamReportingPanel";

const originalCreateObjectURL = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const originalRevokeObjectURL = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
const now = "2026-10-19T10:00:00Z";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  restoreUrlMethod("createObjectURL", originalCreateObjectURL);
  restoreUrlMethod("revokeObjectURL", originalRevokeObjectURL);
});

function restoreUrlMethod(name: string, descriptor: PropertyDescriptor | undefined): void {
  if (descriptor === undefined) Reflect.deleteProperty(URL, name);
  else Object.defineProperty(URL, name, descriptor);
}

function createTeam(overrides: Partial<TeamDetail> = {}): TeamDetail {
  return {
    id: "team-1",
    name: "协作组",
    owner_id: "owner-1",
    created_at: now,
    my_role: "owner",
    my_permissions: [],
    members: [
      { user_id: "owner-1", username: "负责人", avatar_url: null, role: "owner", permissions: [], joined_at: now },
      { user_id: "member-1", username: "小林", avatar_url: null, role: "member", permissions: [], joined_at: now },
      { user_id: "admin-1", username: "管理员", avatar_url: null, role: "admin", permissions: ["view_tasks", "manage_invites"], joined_at: now },
    ],
    ...overrides,
  };
}

function tasksPage(page = 1, pageSize = 10, tasks: TeamMemberTasksPage["tasks"] = []): TeamMemberTasksPage {
  return { total: tasks.length, tasks, page, page_size: pageSize, credits: -3 };
}

function task(id: string): TeamMemberTasksPage["tasks"][number] {
  return {
    id,
    type: "image_generation",
    message_id: null,
    status: "completed",
    result: null,
    request_data: null,
    credit_cost: 0,
    progress: 100,
    progress_message: null,
    created_at: now,
    updated_at: now,
    asset_type: null,
    asset_id: null,
    asset_name: null,
    chapter_title: null,
    chapter_id: null,
    frame_index: null,
    frame_count: null,
    frame_text: null,
  };
}

function teamUsage(): TeamUsageResponse {
  return {
    items: [{
      series_id: "series-1",
      series_name: "北境来信",
      updated_at: now,
      total_calls: 2,
      total_credits: -3,
      failed_calls: 1,
      by_model: [{ model_name: "chat-model", calls: 2, credits: -3, failed_calls: 1 }],
      by_member: [{ user_id: "member-1", username: "小林", avatar_url: null, calls: 2, credits: -3, failed_calls: 1 }],
    }],
    total_calls: 2,
    total_credits: -3,
    total_failed: 1,
  };
}

function seriesUsage(): TeamSeriesUsageResponse {
  const totals = {
    calls: 2,
    credits: -3,
    failed_calls: 1,
    by_model: [{ model_name: "chat-model", calls: 2, credits: -3, failed_calls: 1 }],
    by_member: [{ user_id: "member-1", username: "小林", avatar_url: null, calls: 2, credits: -3, failed_calls: 1 }],
  };
  return {
    series_id: "series-1",
    series_name: "北境来信",
    series_total: totals,
    chapters: [{ ...totals, chapter_id: "chapter-1", chapter_title: "第一章" }],
  };
}

function modelUsage(): TeamModelUsageResponse {
  return {
    model_name: "chat-model",
    total_calls: 2,
    total_credits: -3,
    failed_calls: 1,
    by_series: [{ series_id: "series-1", series_name: "北境来信", calls: 2, credits: -3, failed_calls: 1 }],
    by_member: [{ user_id: "member-1", username: "小林", avatar_url: null, calls: 2, credits: -3, failed_calls: 1 }],
  };
}

function usageExport(): TeamUsageExport {
  return {
    rows: [],
    members: [],
    model_order: [],
    total_calls: 0,
    total_credits: 0,
    total_failed: 0,
  };
}

function createReportingPort(overrides: Partial<TeamReportingServicePort> = {}): TeamReportingServicePort {
  return {
    listMemberTasks: vi.fn(async (_teamId, _userId, page, pageSize) => tasksPage(page, pageSize)),
    getTeamUsage: vi.fn(async () => teamUsage()),
    getSeriesUsage: vi.fn(async () => seriesUsage()),
    getModelUsage: vi.fn(async () => modelUsage()),
    exportTeamUsage: vi.fn(async () => usageExport()),
    ...overrides,
  };
}

function panelProps(overrides: Partial<{
  active: boolean;
  actorId: string;
  sessionGeneration: number;
  services: WorkspaceServices;
  team: TeamDetail;
  port: TeamReportingServicePort | undefined;
  onUnauthorized: () => void;
  onRefreshTeam: () => void;
}> = {}) {
  const port = createReportingPort();
  const services: WorkspaceServices = { ...createDemoServices(), teamReporting: port };
  return {
    active: true,
    actorId: "owner-1",
    sessionGeneration: 3,
    services,
    team: createTeam(),
    port,
    onUnauthorized: vi.fn(),
    onRefreshTeam: vi.fn(),
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => {
    resolve = complete;
    reject = fail;
  });
  return { promise, resolve, reject };
}

describe("TeamReportingPanel", () => {
  it("keeps an admin with only view_tasks away from usage and treats a missing port as disconnected", async () => {
    const adminPort = createReportingPort();
    const admin = panelProps({
      actorId: "admin-1",
      team: createTeam({ owner_id: "owner-1", my_role: "admin", my_permissions: ["view_tasks"] }),
      port: adminPort,
    });
    const view = render(<TeamReportingPanel {...admin} />);

    await screen.findByText("该成员当前没有任务记录。");
    expect(adminPort.listMemberTasks).toHaveBeenCalledWith("team-1", "admin-1", 1, 10, expect.any(AbortSignal));
    expect(adminPort.getTeamUsage).not.toHaveBeenCalled();
    expect(screen.queryByRole("tab", { name: "团队用量" })).not.toBeInTheDocument();

    view.unmount();
    const disconnected = panelProps({ port: undefined });
    render(<TeamReportingPanel {...disconnected} />);
    expect(screen.getByText("当前服务没有接入团队报告接口。"));
    expect(disconnected.services.teamReporting?.listMemberTasks).not.toHaveBeenCalled();
  });

  it("shows usage only when a member has explicit view_usage permission", async () => {
    const port = createReportingPort();
    const props = panelProps({
      actorId: "member-1",
      team: createTeam({
        my_role: "member",
        my_permissions: ["view_usage"],
      }),
      port,
    });
    render(<TeamReportingPanel {...props} />);

    await screen.findByRole("table", { name: "团队用量明细" });
    expect(port.getTeamUsage).toHaveBeenCalledWith("team-1", {}, expect.any(AbortSignal));
    expect(port.listMemberTasks).not.toHaveBeenCalled();
    expect(screen.getByText(/小林 · 北境来信 · 2 次 · -3 积分 · 失败 1/)).toBeInTheDocument();
  });

  it("does not request reports for a member without either reporting permission", () => {
    const port = createReportingPort();
    render(<TeamReportingPanel {...panelProps({
      actorId: "member-1",
      team: createTeam({ my_role: "member", my_permissions: [] }),
      port,
    })} />);

    expect(screen.getByText("当前账号没有查看团队任务或用量的权限。")).toBeInTheDocument();
    expect(port.listMemberTasks).not.toHaveBeenCalled();
    expect(port.getTeamUsage).not.toHaveBeenCalled();
    expect(port.getSeriesUsage).not.toHaveBeenCalled();
    expect(port.getModelUsage).not.toHaveBeenCalled();
    expect(port.exportTeamUsage).not.toHaveBeenCalled();
  });

  it("keeps the loaded task result when the selected member-task tab is clicked again", async () => {
    const user = userEvent.setup();
    const listMemberTasks = vi.fn(async (_teamId: string, _userId: string, page: number, pageSize: number) => ({
      ...tasksPage(page, pageSize, [{ ...task("task-1"), asset_name: "任务结果保留" }]),
      total: 1,
    }));
    const port = createReportingPort({ listMemberTasks });
    render(<TeamReportingPanel {...panelProps({ port })} />);
    await screen.findByText("任务结果保留");

    await user.click(screen.getByRole("tab", { name: "成员任务" }));

    expect(screen.getByText("任务结果保留")).toBeInTheDocument();
    expect(listMemberTasks).toHaveBeenCalledTimes(1);
  });

  it("keeps the loaded team-usage overview when the selected usage tab is clicked again", async () => {
    const user = userEvent.setup();
    const getTeamUsage = vi.fn(async () => teamUsage());
    const port = createReportingPort({ getTeamUsage });
    render(<TeamReportingPanel {...panelProps({ port })} />);
    await user.click(screen.getByRole("tab", { name: "团队用量" }));
    await screen.findByRole("table", { name: "团队用量明细" });

    await user.click(screen.getByRole("tab", { name: "团队用量" }));

    expect(screen.getByRole("table", { name: "团队用量明细" })).toBeInTheDocument();
    expect(getTeamUsage).toHaveBeenCalledTimes(1);
  });

  it("returns to the team-usage overview when its type tab is clicked from series or model detail", async () => {
    const user = userEvent.setup();
    const getTeamUsage = vi.fn(async () => teamUsage());
    const port = createReportingPort({ getTeamUsage });
    render(<TeamReportingPanel {...panelProps({ port })} />);
    await user.click(screen.getByRole("tab", { name: "团队用量" }));
    await screen.findByRole("table", { name: "团队用量明细" });

    await user.click(screen.getByRole("button", { name: "北境来信" }));
    await screen.findByRole("heading", { name: "北境来信" });
    await user.click(screen.getByRole("tab", { name: "团队用量" }));
    await screen.findByRole("table", { name: "团队用量明细" });
    expect(getTeamUsage).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole("button", { name: "北境来信" }));
    await screen.findByRole("heading", { name: "北境来信" });
    await user.click(screen.getByRole("button", { name: "chat-model · 2 次" }));
    await screen.findByRole("heading", { name: "chat-model" });
    await user.click(screen.getByRole("tab", { name: "团队用量" }));
    await screen.findByRole("table", { name: "团队用量明细" });
    expect(getTeamUsage).toHaveBeenCalledTimes(3);
  });

  it("uses the selected member and requested page for member-task reads", async () => {
    const user = userEvent.setup();
    const listMemberTasks = vi.fn(async (_teamId: string, userId: string, page: number, pageSize: number) => ({
      ...tasksPage(page, pageSize, [{ ...task(`${userId}-${page}`), asset_name: `${userId}-${page}` }]),
      total: 21,
    }));
    const port = createReportingPort({ listMemberTasks });
    render(<TeamReportingPanel {...panelProps({ port })} />);

    await screen.findByText("owner-1-1");
    await user.selectOptions(screen.getByLabelText("成员"), "member-1");
    await screen.findByText("member-1-1");
    expect(listMemberTasks).toHaveBeenLastCalledWith("team-1", "member-1", 1, 10, expect.any(AbortSignal));

    await user.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("member-1-2");
    expect(listMemberTasks).toHaveBeenLastCalledWith("team-1", "member-1", 2, 10, expect.any(AbortSignal));
  });

  it.each(["session generation", "team identity", "services identity", "actor identity", "active state"] as const)(
    "ignores a late task response after the %s changes",
    async (changedScope) => {
      const stale = deferred<TeamMemberTasksPage>();
      const listMemberTasks = vi.fn()
        .mockImplementationOnce(() => stale.promise)
        .mockResolvedValue(tasksPage());
      const port = createReportingPort({ listMemberTasks });
      const onUnauthorized = vi.fn();
      const props = panelProps({ port, onUnauthorized });
      const view = render(<TeamReportingPanel {...props} />);
      await waitFor(() => expect(listMemberTasks).toHaveBeenCalledTimes(1));

      if (changedScope === "session generation") {
        view.rerender(<TeamReportingPanel {...props} sessionGeneration={props.sessionGeneration + 1} />);
      } else if (changedScope === "team identity") {
        view.rerender(<TeamReportingPanel {...props} team={createTeam({ id: "team-2" })} />);
      } else if (changedScope === "services identity") {
        view.rerender(<TeamReportingPanel {...props} services={{ ...props.services }} />);
      } else if (changedScope === "actor identity") {
        view.rerender(<TeamReportingPanel
          {...props}
          actorId="member-1"
          team={createTeam({ my_role: "member", my_permissions: ["view_tasks"] })}
        />);
      } else {
        view.rerender(<TeamReportingPanel {...props} active={false} />);
      }

      if (changedScope !== "active state") await screen.findByText("该成员当前没有任务记录。");
      await act(async () => {
        stale.resolve(tasksPage(1, 10, [task("stale-task")]));
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(screen.queryByText("stale-task")).not.toBeInTheDocument();
      expect(onUnauthorized).not.toHaveBeenCalled();
    },
  );

  it("converts valid years below 0100 without the Date constructor's 1900 offset", async () => {
    const user = userEvent.setup();
    const port = createReportingPort();
    render(<TeamReportingPanel {...panelProps({
      actorId: "member-1",
      team: createTeam({ my_role: "member", my_permissions: ["view_usage"] }),
      port,
    })} />);
    await screen.findByRole("table", { name: "团队用量明细" });

    fireEvent.change(screen.getByLabelText("开始日期"), { target: { value: "0099-01-01" } });
    fireEvent.change(screen.getByLabelText("结束日期"), { target: { value: "0099-01-01" } });
    await user.click(screen.getByRole("button", { name: "应用筛选" }));

    await waitFor(() => expect(port.getTeamUsage).toHaveBeenLastCalledWith(
      "team-1",
      expect.objectContaining({ startTime: expect.any(String), endTime: expect.any(String) }),
      expect.any(AbortSignal),
    ));
    const range = vi.mocked(port.getTeamUsage).mock.lastCall?.[1];
    expect(new Date(range?.startTime ?? "").getUTCFullYear()).toBeLessThan(100);
    expect(new Date(range?.endTime ?? "").getUTCFullYear()).toBeLessThan(100);
  });

  it("reaches tasks, usage, series, model and export with one applied date snapshot", async () => {
    const user = userEvent.setup();
    const port = createReportingPort();
    render(<TeamReportingPanel {...panelProps({ port })} />);
    await screen.findByText("该成员当前没有任务记录。");

    await user.click(screen.getByRole("tab", { name: "团队用量" }));
    await screen.findByRole("table", { name: "团队用量明细" });
    fireEvent.change(screen.getByLabelText("开始日期"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByLabelText("结束日期"), { target: { value: "2026-10-31" } });
    await user.click(screen.getByRole("button", { name: "应用筛选" }));
    await waitFor(() => expect(port.getTeamUsage).toHaveBeenLastCalledWith(
      "team-1",
      {
        startTime: new Date(2026, 9, 1).toISOString(),
        endTime: new Date(2026, 9, 31, 23, 59, 59, 999).toISOString(),
      },
      expect.any(AbortSignal),
    ));

    await user.click(screen.getByRole("button", { name: "北境来信" }));
    await screen.findByRole("heading", { name: "北境来信" });
    expect(port.getSeriesUsage).toHaveBeenLastCalledWith(
      "team-1",
      "series-1",
      expect.objectContaining({ startTime: expect.any(String), endTime: expect.any(String) }),
      expect.any(AbortSignal),
    );
    expect(screen.getByText(/小林 · 2 次 · -3 积分 · 失败 1/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "chat-model · 2 次" }));
    await screen.findByRole("heading", { name: "chat-model" });
    expect(port.getModelUsage).toHaveBeenLastCalledWith(
      "team-1",
      "chat-model",
      expect.objectContaining({ startTime: expect.any(String), endTime: expect.any(String) }),
      expect.any(AbortSignal),
    );
    await user.click(screen.getByRole("button", { name: "‹ 返回剧集明细" }));
    expect(await screen.findByRole("table", { name: "章节用量" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "‹ 返回团队用量" }));
    await screen.findByRole("table", { name: "团队用量明细" });

    const createObjectURL = vi.fn(() => "blob:team-report");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await user.click(screen.getByRole("button", { name: "导出 Excel" }));
    await waitFor(() => expect(port.exportTeamUsage).toHaveBeenCalledWith(
      "team-1",
      expect.objectContaining({ startTime: expect.any(String), endTime: expect.any(String) }),
      expect.any(AbortSignal),
    ));
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
  });

  it.each(["success", "401"] as const)("ignores a late %s after the reporting port changes in the same services object", async (lateResult) => {
    const stale = deferred<TeamMemberTasksPage>();
    const oldPort = createReportingPort({ listMemberTasks: vi.fn(() => stale.promise) });
    const services: WorkspaceServices = { ...createDemoServices(), teamReporting: oldPort };
    const onUnauthorized = vi.fn();
    const props = panelProps({ services, port: oldPort, onUnauthorized });
    const view = render(<TeamReportingPanel {...props} />);
    await waitFor(() => expect(oldPort.listMemberTasks).toHaveBeenCalledTimes(1));

    const nextPort = createReportingPort();
    Object.assign(services, { teamReporting: nextPort });
    view.rerender(<TeamReportingPanel {...props} port={services.teamReporting} />);
    await screen.findByText("该成员当前没有任务记录。");

    await act(async () => {
      if (lateResult === "success") stale.resolve(tasksPage(1, 10, [task("stale-task")]));
      else stale.reject(new ApiError("http", "旧会话拒绝", 401));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.queryByText("stale-task")).not.toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it.each([
    ["inactive tab", "success"],
    ["team identity", "401"],
    ["session generation", "success"],
  ] as const)("ignores a late export after a scope change (%s; %s)", async (changedScope, lateResult) => {
    const user = userEvent.setup();
    const pending = deferred<TeamUsageExport>();
    const port = createReportingPort({ exportTeamUsage: vi.fn(() => pending.promise) });
    const props = panelProps({ port });
    const view = render(<TeamReportingPanel {...props} />);
    await screen.findByText("该成员当前没有任务记录。");
    await user.click(screen.getByRole("tab", { name: "团队用量" }));
    await screen.findByRole("table", { name: "团队用量明细" });

    const createObjectURL = vi.fn(() => "blob:late-report");
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await user.click(screen.getByRole("button", { name: "导出 Excel" }));
    await waitFor(() => expect(port.exportTeamUsage).toHaveBeenCalledTimes(1));
    if (changedScope === "inactive tab") {
      view.rerender(<TeamReportingPanel {...props} active={false} />);
    } else if (changedScope === "team identity") {
      view.rerender(<TeamReportingPanel {...props} team={createTeam({ id: "team-2" })} />);
    } else {
      view.rerender(<TeamReportingPanel {...props} sessionGeneration={props.sessionGeneration + 1} />);
    }

    await act(async () => {
      if (lateResult === "success") pending.resolve(usageExport());
      else pending.reject(new ApiError("http", "旧会话拒绝", 401));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();
  });

  it("reports a failed read and retries without presenting it as an empty report", async () => {
    const user = userEvent.setup();
    const getTeamUsage = vi.fn()
      .mockRejectedValueOnce(new ApiError("http", "报告服务暂停", 503))
      .mockResolvedValue(teamUsage());
    const port = createReportingPort({ getTeamUsage });
    const props = panelProps({
      actorId: "member-1",
      team: createTeam({ my_role: "member", my_permissions: ["view_usage"] }),
      port,
    });
    render(<TeamReportingPanel {...props} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("当前服务没有接入团队报告接口。");
    expect(screen.queryByText("当前日期范围内没有团队剧集用量。")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByRole("table", { name: "团队用量明细" });
    expect(getTeamUsage).toHaveBeenCalledTimes(2);
  });

  it("refreshes team permissions after a reporting permission denial", async () => {
    const getTeamUsage = vi.fn(async () => { throw new ApiError("http", "无权查看", 403); });
    const port = createReportingPort({ getTeamUsage });
    const onRefreshTeam = vi.fn();
    render(<TeamReportingPanel {...panelProps({
      actorId: "member-1",
      team: createTeam({ my_role: "member", my_permissions: ["view_usage"] }),
      port,
      onRefreshTeam,
    })} />);

    await screen.findByText("当前账号没有查看团队任务或用量的权限。");
    expect(onRefreshTeam).toHaveBeenCalledTimes(1);
    expect(getTeamUsage).toHaveBeenCalledTimes(1);
  });
});
