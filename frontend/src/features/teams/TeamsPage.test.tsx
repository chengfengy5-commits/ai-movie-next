import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createDemoServices, type WorkspaceServices } from "../../shared/api/services";
import { ApiError } from "../../shared/api/errors";
import type { TeamDetail, TeamInvite, TeamManagementServicePort, TeamPermission } from "../../shared/api/teamManagement";
import { TeamsPage } from "./TeamsPage";

const initialClipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  if (initialClipboardDescriptor === undefined) {
    Reflect.deleteProperty(navigator, "clipboard");
  } else {
    Object.defineProperty(navigator, "clipboard", initialClipboardDescriptor);
  }
});

const now = "2026-10-17T12:00:00Z";
const team = { id: "team-a", name: "协作组", owner_id: "owner-1", created_at: now };
const detail: TeamDetail = {
  ...team,
  my_role: "owner",
  my_permissions: [],
  members: [{
    user_id: "owner-1", username: "Team owner", avatar_url: null, role: "owner", permissions: [], joined_at: now,
  }, {
    user_id: "member-1", username: "Team member", avatar_url: null, role: "member", permissions: [], joined_at: now,
  }],
};

function createPort(overrides: Partial<TeamManagementServicePort> = {}): TeamManagementServicePort {
  return {
    listTeams: vi.fn(async () => []),
    createTeam: vi.fn(async () => team),
    getTeam: vi.fn(async () => detail),
    updateTeam: vi.fn(async (id, _update, name) => ({ id, name })),
    deleteTeam: vi.fn(async () => ({ message: "团队已解散" })),
    leaveTeam: vi.fn(async () => ({ message: "已退出团队" })),
    removeMember: vi.fn(async () => ({ message: "成员已移除" })),
    setMemberRole: vi.fn(async () => ({ message: "角色已更新", role: "admin" as const, permissions: ["view_tasks", "manage_invites"] as TeamPermission[] })),
    setMemberPermissions: vi.fn(async () => ({ message: "权限已更新", permissions: ["view_tasks"] as TeamPermission[] })),
    createInvites: vi.fn(async () => ({ invites: [{ id: "invite-1", code: "A1B2C3D4", expires_at: now }], ttl_hours: 24 })),
    listInvites: vi.fn(async () => []),
    revokeInvite: vi.fn(async () => ({ message: "邀请码已作废" })),
    joinTeam: vi.fn(async () => ({ message: "已加入团队", team_id: team.id, team_name: team.name })),
    ...overrides,
  };
}

function renderTeamsPage(
  port: TeamManagementServicePort,
  onGuardChange = vi.fn(),
  onUnauthorized = vi.fn(),
) {
  const services: WorkspaceServices = { ...createDemoServices(), teamManagement: port };
  const result = render(
    <TeamsPage
      services={services}
      userId="owner-1"
      sessionGeneration={3}
      guardScopeKey="owner-1:3:workspace-0"
      onUnauthorized={onUnauthorized}
      onBack={vi.fn()}
      onGuardChange={onGuardChange}
      onDirectoryChanged={vi.fn()}
    />,
  );
  return { ...result, services, onGuardChange, onUnauthorized };
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

describe("TeamsPage", () => {
  it("keeps a confirmed generated-code receipt visible when the follow-up invite read fails", async () => {
    const user = userEvent.setup();
    const listInvites = vi.fn(async (): Promise<TeamInvite[]> => { throw new ApiError("network", "offline"); });
    const port = createPort({
      createTeam: vi.fn(async () => team),
      getTeam: vi.fn(async () => detail),
      listInvites,
    });
    const { onGuardChange } = renderTeamsPage(port);

    await screen.findByText("当前账号还没有加入团队。");
    await user.type(screen.getByLabelText("团队名称"), "新团队");
    expect(onGuardChange).toHaveBeenLastCalledWith("owner-1:3:workspace-0", true, false);
    await user.click(screen.getByRole("button", { name: "创建团队" }));
    await screen.findByRole("heading", { name: "协作组" });
    await user.click(screen.getByRole("tab", { name: "邀请码" }));
    await user.click(screen.getByRole("button", { name: "生成邀请码" }));

    expect(await screen.findByText("A1B2C3D4")).toBeInTheDocument();
    expect(screen.getByText(/本次生成已确认/)).toBeInTheDocument();
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    await user.click(screen.getByRole("button", { name: "复制邀请码" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("A1B2C3D4"));
    expect(screen.getByText("邀请码已复制。", { exact: true })).toBeInTheDocument();
    expect(screen.getByText(/服务器返回内容异常/)).toBeInTheDocument();
    expect(port.createInvites).toHaveBeenCalledWith(team.id, 1, expect.any(AbortSignal));
    expect(listInvites).toHaveBeenCalledTimes(1);
    expect(onGuardChange).toHaveBeenLastCalledWith("owner-1:3:workspace-0", true, false);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await user.click(screen.getByRole("tab", { name: "团队设置" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("回执"));
    expect(screen.getByText("A1B2C3D4")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成邀请码" })).toBeDisabled();
    vi.mocked(window.confirm).mockReturnValue(true);
    await user.click(screen.getByRole("tab", { name: "成员与权限" }));
    expect(screen.queryByText("A1B2C3D4")).not.toBeInTheDocument();
    expect(onGuardChange).toHaveBeenLastCalledWith("owner-1:3:workspace-0", false, false);
    expect(port.createInvites).toHaveBeenCalledTimes(1);
  });

  it("freezes an unknown write, permits only a read-only snapshot, and requires explicit abandon before new intent", async () => {
    const user = userEvent.setup();
    const port = createPort({
      createTeam: vi.fn(async () => { throw new ApiError("timeout", "pending"); }),
    });
    const onGuardChange = vi.fn();
    renderTeamsPage(port, onGuardChange);
    await screen.findByText("当前账号还没有加入团队。");
    const nameInput = screen.getByLabelText("团队名称");
    await user.type(nameInput, "新团队");
    await user.click(screen.getByRole("button", { name: "创建团队" }));

    expect(await screen.findByRole("region", { name: "待核对的团队操作" })).toBeInTheDocument();
    expect(nameInput).toHaveValue("新团队");
    expect(screen.getByRole("button", { name: "创建团队" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "只读核对" }));
    expect(await screen.findByRole("status", { name: "只读核对快照" })).toHaveTextContent("当前团队数量：0");
    expect(port.createTeam).toHaveBeenCalledTimes(1);
    expect(port.listTeams).toHaveBeenCalledTimes(2);

    vi.spyOn(window, "confirm").mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "明确放弃本地状态" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "待核对的团队操作" })).not.toBeInTheDocument());
    expect(screen.getByLabelText("团队名称")).toHaveValue("");
    expect(onGuardChange).toHaveBeenLastCalledWith("owner-1:3:workspace-0", false, false);
    expect(port.createTeam).toHaveBeenCalledTimes(1);
  });

  it("preserves dirty forms when the user cancels a team switch", async () => {
    const user = userEvent.setup();
    const port = createPort({
      listTeams: vi.fn(async () => [
        { ...team, member_count: 2, my_role: "owner" },
        { id: "team-b", name: "第二团队", owner_id: "owner-1", created_at: now, member_count: 1, my_role: "owner" },
      ]),
    });
    renderTeamsPage(port);
    await user.click(await screen.findByRole("button", { name: /协作组/ }));
    await screen.findByRole("heading", { name: "协作组" });
    await user.click(screen.getByRole("tab", { name: "团队设置" }));
    const nameInput = screen.getByLabelText("团队名称");
    await user.clear(nameInput);
    await user.type(nameInput, "尚未保存");
    vi.spyOn(window, "confirm").mockReturnValue(false);

    await user.click(screen.getByRole("button", { name: "‹ 返回团队列表" }));
    expect(nameInput).toHaveValue("尚未保存");
    expect(screen.getByRole("heading", { name: "协作组" })).toBeInTheDocument();
    expect(port.updateTeam).not.toHaveBeenCalled();
  });

  it("preserves a dirty team form when cancelling a management-panel change", async () => {
    const user = userEvent.setup();
    const port = createPort({
      listTeams: vi.fn(async () => [{ ...team, member_count: 2, my_role: "owner" }]),
    });
    renderTeamsPage(port);
    await user.click(await screen.findByRole("button", { name: /协作组/ }));
    await screen.findByRole("heading", { name: "协作组" });
    await user.click(screen.getByRole("tab", { name: "团队设置" }));
    const nameInput = screen.getByLabelText("团队名称");
    await user.clear(nameInput);
    await user.type(nameInput, "面板草稿");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);

    await user.click(screen.getByRole("tab", { name: "成员与权限" }));

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("未保存"));
    expect(screen.getByRole("tabpanel", { name: "团队设置" })).toBeInTheDocument();
    expect(screen.getByLabelText("团队名称")).toHaveValue("面板草稿");
    expect(port.updateTeam).not.toHaveBeenCalled();
  });

  it.each(["401", "403", "success"] as const)(
    "does not apply a pending invite response after switching to the member view (%s)",
    async (outcome) => {
      const user = userEvent.setup();
      const pending = deferred<TeamInvite[]>();
      const staleInvite: TeamInvite = {
        id: "stale-invite",
        code: "STALE123",
        status: "active",
        created_at: now,
        expires_at: now,
        used_by: null,
        used_at: null,
      };
      const listInvites = vi.fn((_teamId: string, _signal: AbortSignal) => pending.promise);
      const port = createPort({
        listTeams: vi.fn(async () => [{ ...team, member_count: 2, my_role: "owner" }]),
        listInvites,
      });
      const onUnauthorized = vi.fn();
      renderTeamsPage(port, vi.fn(), onUnauthorized);

      await user.click(await screen.findByRole("button", { name: /协作组/ }));
      await user.click(screen.getByRole("tab", { name: "邀请码" }));
      await user.click(screen.getByRole("button", { name: "读取邀请码" }));
      await waitFor(() => expect(listInvites).toHaveBeenCalledTimes(1));
      const requestSignal = listInvites.mock.calls[0]?.[1];

      await user.click(screen.getByRole("tab", { name: "成员与权限" }));
      await user.selectOptions(screen.getByLabelText("角色"), "admin");
      expect(screen.getByLabelText("角色")).toHaveValue("admin");

      await act(async () => {
        if (outcome === "401") pending.reject(new ApiError("http", "expired", 401));
        else if (outcome === "403") pending.reject(new ApiError("http", "forbidden", 403));
        else pending.resolve([staleInvite]);
        await pending.promise.catch(() => undefined);
      });

      expect(screen.getByLabelText("角色")).toHaveValue("admin");
      if (outcome === "401") {
        expect(onUnauthorized).not.toHaveBeenCalled();
      } else if (outcome === "403") {
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      } else {
        vi.spyOn(window, "confirm").mockReturnValue(true);
        await user.click(screen.getByRole("tab", { name: "邀请码" }));
        expect(screen.queryByText("STALE123")).not.toBeInTheDocument();
      }
      expect(requestSignal?.aborted).toBe(true);
    },
  );

  it("retires invite loading on tab exit and still honors a current-view 401", async () => {
    const user = userEvent.setup();
    const pending = deferred<TeamInvite[]>();
    const listInvites = vi.fn((_teamId: string, _signal: AbortSignal) => pending.promise);
    const onUnauthorized = vi.fn();
    const port = createPort({
      listTeams: vi.fn(async () => [{ ...team, member_count: 2, my_role: "owner" }]),
      listInvites,
    });
    renderTeamsPage(port, vi.fn(), onUnauthorized);

    await user.click(await screen.findByRole("button", { name: /协作组/ }));
    await user.click(screen.getByRole("tab", { name: "邀请码" }));
    await user.click(screen.getByRole("button", { name: "读取邀请码" }));
    await waitFor(() => expect(listInvites).toHaveBeenCalledTimes(1));
    const requestSignal = listInvites.mock.calls[0]?.[1];

    await user.click(screen.getByRole("tab", { name: "成员与权限" }));
    await user.click(screen.getByRole("tab", { name: "邀请码" }));
    expect(screen.getByRole("button", { name: "读取邀请码" })).toBeEnabled();
    expect(requestSignal?.aborted).toBe(true);

    await act(async () => {
      pending.reject(new ApiError("http", "expired", 401));
      await pending.promise.catch(() => undefined);
    });
    expect(onUnauthorized).not.toHaveBeenCalled();

    const currentRequest = deferred<TeamInvite[]>();
    listInvites.mockImplementationOnce(() => currentRequest.promise);
    await user.click(screen.getByRole("button", { name: "读取邀请码" }));
    await waitFor(() => expect(listInvites).toHaveBeenCalledTimes(2));
    await act(async () => {
      currentRequest.reject(new ApiError("http", "expired", 401));
      await currentRequest.promise.catch(() => undefined);
    });
    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
  });

  it("lets the owner manage permissions even when owner my_permissions is empty", async () => {
    const user = userEvent.setup();
    let serverDetail = detail;
    const setMemberRole = vi.fn(async (_teamId: string, userId: string, role: "admin" | "member") => {
      const permissions: TeamPermission[] = role === "admin" ? ["view_tasks", "manage_invites"] : [];
      serverDetail = {
        ...serverDetail,
        members: serverDetail.members.map((member) => member.user_id === userId
          ? { ...member, role, permissions }
          : member),
      };
      return { message: "角色已更新", role, permissions };
    });
    const setMemberPermissions = vi.fn(async (_teamId: string, userId: string, permissions: TeamPermission[]) => {
      const normalized = [...new Set(permissions)];
      serverDetail = {
        ...serverDetail,
        members: serverDetail.members.map((member) => member.user_id === userId
          ? { ...member, permissions: normalized }
          : member),
      };
      return { message: "权限已更新", permissions: normalized };
    });
    const port = createPort({
      listTeams: vi.fn(async () => [{ ...team, member_count: 2, my_role: "owner" }]),
      getTeam: vi.fn(async () => serverDetail),
      setMemberRole,
      setMemberPermissions,
    });
    renderTeamsPage(port);
    await user.click(await screen.findByRole("button", { name: /协作组/ }));
    await screen.findByRole("heading", { name: "协作组" });
    expect(screen.getByRole("tab", { name: "邀请码" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("角色"), "admin");
    await user.click(screen.getByRole("button", { name: "保存角色" }));
    await screen.findByLabelText("查看团队任务");
    expect(setMemberRole).toHaveBeenCalledWith(team.id, "member-1", "admin", "member", [], expect.any(AbortSignal));
    expect(screen.getByLabelText("管理邀请码")).toBeChecked();
    expect(screen.getAllByRole("checkbox")).toHaveLength(6);

    await user.click(screen.getByLabelText("查看团队用量"));
    await user.click(screen.getByRole("button", { name: "保存权限" }));
    await waitFor(() => expect(setMemberPermissions).toHaveBeenCalledWith(
      team.id,
      "member-1",
      ["view_tasks", "manage_invites", "view_usage"],
      expect.any(AbortSignal),
    ));
    expect(screen.getByLabelText("查看团队用量")).toBeChecked();

    await user.click(screen.getByLabelText("查看团队任务"));
    await user.click(screen.getByLabelText("查看团队用量"));
    await user.click(screen.getByLabelText("管理邀请码"));
    await user.click(screen.getByRole("button", { name: "保存权限" }));
    await waitFor(() => expect(setMemberPermissions).toHaveBeenLastCalledWith(
      team.id, "member-1", [], expect.any(AbortSignal),
    ));
    expect(screen.getAllByRole("checkbox").every((checkbox) => !(checkbox as HTMLInputElement).checked)).toBe(true);

    await user.selectOptions(screen.getByLabelText("角色"), "member");
    await user.click(screen.getByRole("button", { name: "保存角色" }));
    await waitFor(() => expect(setMemberRole).toHaveBeenLastCalledWith(
      team.id,
      "member-1",
      "member",
      "admin",
      [],
      expect.any(AbortSignal),
    ));
    expect(screen.queryByLabelText("查看团队任务")).not.toBeInTheDocument();
  });

  it("joins with the entered code and opens the server-selected team", async () => {
    const user = userEvent.setup();
    const joinedTeam = { ...team, id: "joined-team", name: "新加入团队", owner_id: "captain-1" };
    const joinedDetail: TeamDetail = {
      ...joinedTeam,
      my_role: "member",
      my_permissions: [],
      members: [
        { user_id: "captain-1", username: "Team owner", avatar_url: null, role: "owner", permissions: [], joined_at: now },
        { user_id: "owner-1", username: "Current user", avatar_url: null, role: "member", permissions: [], joined_at: now },
      ],
    };
    const joinTeam = vi.fn(async () => ({
      message: "已加入团队", team_id: joinedTeam.id, team_name: joinedTeam.name,
    }));
    const port = createPort({
      listTeams: vi.fn().mockResolvedValueOnce([]).mockResolvedValueOnce([
        { ...joinedTeam, member_count: 2, my_role: "member" },
      ]),
      getTeam: vi.fn(async () => joinedDetail),
      joinTeam,
    });
    renderTeamsPage(port);
    await screen.findByText("当前账号还没有加入团队。");
    await user.type(screen.getByLabelText("邀请码"), "  ABCDEF  ");
    await user.click(screen.getByRole("button", { name: "加入团队" }));

    expect(await screen.findByRole("heading", { name: "新加入团队" })).toBeInTheDocument();
    expect(joinTeam).toHaveBeenCalledWith("  ABCDEF  ", expect.any(AbortSignal));
    expect(screen.getByRole("status")).toHaveTextContent("加入团队已确认");
  });

  it("saves a trimmed team name and requires confirmation before disbanding", async () => {
    const user = userEvent.setup();
    let serverDetail = detail;
    const updateTeam = vi.fn(async (teamId: string, update: { name?: string | null }) => {
      const name = update.name?.trim() ?? serverDetail.name;
      serverDetail = { ...serverDetail, name };
      return { id: teamId, name };
    });
    const port = createPort({
      listTeams: vi.fn()
        .mockResolvedValueOnce([{ ...team, member_count: 2, my_role: "owner" }])
        .mockResolvedValueOnce([{ ...team, name: "新的团队名称", member_count: 2, my_role: "owner" }])
        .mockResolvedValueOnce([]),
      getTeam: vi.fn(async () => serverDetail),
      updateTeam,
    });
    renderTeamsPage(port);
    await user.click(await screen.findByRole("button", { name: /协作组/ }));
    await screen.findByRole("heading", { name: "协作组" });
    await user.click(screen.getByRole("tab", { name: "团队设置" }));
    const nameInput = screen.getByLabelText("团队名称");
    await user.clear(nameInput);
    await user.type(nameInput, "  新的团队名称  ");
    await user.click(screen.getByRole("button", { name: "保存名称" }));

    expect(await screen.findByRole("heading", { name: "新的团队名称" })).toBeInTheDocument();
    expect(updateTeam).toHaveBeenCalledWith(team.id, { name: "  新的团队名称  " }, team.name, expect.any(AbortSignal));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "解散团队" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("不可撤回"));
    await screen.findByText("当前账号还没有加入团队。");
    expect(port.deleteTeam).toHaveBeenCalledWith(team.id, expect.any(AbortSignal));
  });

  it("lets a permitted admin list and revoke invites, remove a member, and leave", async () => {
    const user = userEvent.setup();
    const adminDetail: TeamDetail = {
      ...detail,
      my_role: "admin",
      my_permissions: ["manage_invites", "remove_members"],
      members: [
        detail.members[0]!,
        { ...detail.members[1]!, user_id: "admin-1", username: "Current admin", role: "admin", permissions: ["manage_invites", "remove_members"] },
        { ...detail.members[1]!, user_id: "member-2", username: "Other member" },
      ],
    };
    const invite: TeamInvite = {
      id: "invite-1", code: "QWER1234", status: "active", created_at: now, expires_at: now, used_by: null, used_at: null,
    };
    const revokeInvite = vi.fn(async () => ({ message: "邀请码已作废" }));
    const removeMember = vi.fn(async () => ({ message: "成员已移除" }));
    const leaveTeam = vi.fn(async () => ({ message: "已退出团队" }));
    const listInvites = vi.fn(async () => [invite]);
    const port = createPort({
      listTeams: vi.fn()
        .mockResolvedValueOnce([{ ...team, member_count: 3, my_role: "admin" }])
        .mockResolvedValueOnce([{ ...team, member_count: 2, my_role: "admin" }])
        .mockResolvedValueOnce([]),
      getTeam: vi.fn(async () => adminDetail),
      listInvites,
      revokeInvite,
      removeMember,
      leaveTeam,
    });
    render(
      <TeamsPage
        services={{ ...createDemoServices(), teamManagement: port }}
        userId="admin-1"
        sessionGeneration={7}
        guardScopeKey="admin-1:7:workspace-0"
        onUnauthorized={vi.fn()}
        onBack={vi.fn()}
        onGuardChange={vi.fn()}
        onDirectoryChanged={vi.fn()}
      />,
    );
    await user.click(await screen.findByRole("button", { name: /协作组/ }));
    await user.click(screen.getByRole("tab", { name: "邀请码" }));
    await user.click(screen.getByRole("button", { name: "读取邀请码" }));
    expect(await screen.findByText("QWER1234")).toBeInTheDocument();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "作废" }));
    expect(revokeInvite).toHaveBeenCalledWith(team.id, invite.id, expect.any(AbortSignal));
    expect(listInvites).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole("tab", { name: "成员与权限" }));
    await user.click(screen.getByRole("button", { name: "移除成员" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("移除这位成员"));
    expect(removeMember).toHaveBeenCalledWith(team.id, "member-2", expect.any(AbortSignal));

    await user.click(screen.getByRole("tab", { name: "团队设置" }));
    await user.click(screen.getByRole("button", { name: "退出团队" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("退出此团队"));
    await screen.findByText("当前账号还没有加入团队。");
    expect(leaveTeam).toHaveBeenCalledWith(team.id, expect.any(AbortSignal));
  });

  it("does not surface a late clipboard result after switching teams", async () => {
    const user = userEvent.setup();
    const clipboardPending = deferred<void>();
    const writeText = vi.fn(() => clipboardPending.promise);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const teamB = { ...team, id: "team-b", name: "第二团队" };
    const inviteFor = (teamId: string): TeamInvite => ({
      id: `invite-${teamId}`,
      code: teamId === team.id ? "ATEAM123" : "BTEAM123",
      status: "active",
      created_at: now,
      expires_at: now,
      used_by: null,
      used_at: null,
    });
    const port = createPort({
      listTeams: vi.fn(async () => [
        { ...team, member_count: 2, my_role: "owner" },
        { ...teamB, member_count: 2, my_role: "owner" },
      ]),
      getTeam: vi.fn(async (teamId) => teamId === teamB.id ? { ...detail, ...teamB } : detail),
      listInvites: vi.fn(async (teamId) => [inviteFor(teamId)]),
    });
    renderTeamsPage(port);
    await user.click(await screen.findByRole("button", { name: /协作组/ }));
    await user.click(screen.getByRole("tab", { name: "邀请码" }));
    await user.click(screen.getByRole("button", { name: "读取邀请码" }));
    await screen.findByText("ATEAM123");
    await user.click(screen.getByRole("button", { name: "复制邀请码" }));
    expect(writeText).toHaveBeenCalledWith("ATEAM123");

    await user.click(screen.getByRole("button", { name: "‹ 返回团队列表" }));
    await user.click(screen.getByRole("button", { name: /第二团队/ }));
    await user.click(screen.getByRole("tab", { name: "邀请码" }));
    await user.click(screen.getByRole("button", { name: "读取邀请码" }));
    await screen.findByText("BTEAM123");
    await act(async () => { clipboardPending.resolve(); });

    expect(screen.queryByText("邀请码已复制。", { exact: true })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "第二团队" })).toBeInTheDocument();
  });

  it("publishes the in-flight guard before the first await and rejects a same-tick duplicate write", async () => {
    const pending = deferred<typeof team>();
    const user = userEvent.setup();
    const createTeam = vi.fn(() => pending.promise);
    const onGuardChange = vi.fn();
    const port = createPort({ createTeam });
    renderTeamsPage(port, onGuardChange);
    await screen.findByText("当前账号还没有加入团队。");
    await user.type(screen.getByLabelText("团队名称"), "新团队");
    const createButton = screen.getByRole("button", { name: "创建团队" });

    act(() => {
      fireEvent.click(createButton);
      fireEvent.click(createButton);
    });

    expect(createTeam).toHaveBeenCalledTimes(1);
    expect(onGuardChange).toHaveBeenLastCalledWith("owner-1:3:workspace-0", true, true);
    await act(async () => { pending.resolve(team); });
    expect(await screen.findByRole("heading", { name: "协作组" })).toBeInTheDocument();
  });

  it("ignores an old-session write success without clearing the new session guard", async () => {
    const pending = deferred<typeof team>();
    const oldDirectoryChanged = vi.fn();
    const oldPort = createPort({ createTeam: vi.fn(() => pending.promise) });
    const newTeam = { ...team, id: "team-new", name: "新会话团队", owner_id: "new-owner" };
    const newPort = createPort({
      listTeams: vi.fn(async () => [{ ...newTeam, member_count: 1, my_role: "owner" }]),
      getTeam: vi.fn(async () => ({ ...detail, ...newTeam })),
    });
    const oldServices: WorkspaceServices = { ...createDemoServices(), teamManagement: oldPort };
    const newServices: WorkspaceServices = { ...createDemoServices(), teamManagement: newPort };
    const onUnauthorized = vi.fn();
    const oldGuard = vi.fn();
    const newGuard = vi.fn();
    const newDirectoryChanged = vi.fn();
    const renderPage = (
      services: WorkspaceServices,
      ownerId: string,
      session: number,
      scope: string,
      onGuardChange: (scopeKey: string, dirty: boolean, inFlight: boolean) => void,
      onDirectoryChanged: () => void,
    ) => (
      <TeamsPage
        services={services}
        userId={ownerId}
        sessionGeneration={session}
        guardScopeKey={scope}
        onUnauthorized={onUnauthorized}
        onBack={vi.fn()}
        onGuardChange={onGuardChange}
        onDirectoryChanged={onDirectoryChanged}
      />
    );
    const view = render(renderPage(oldServices, "owner-1", 3, "owner-1:3", oldGuard, oldDirectoryChanged));
    await screen.findByText("当前账号还没有加入团队。");
    await userEvent.type(screen.getByLabelText("团队名称"), "旧会话草稿");
    await userEvent.click(screen.getByRole("button", { name: "创建团队" }));
    await waitFor(() => expect(oldPort.createTeam).toHaveBeenCalledTimes(1));

    view.rerender(renderPage(newServices, "new-owner", 4, "new-owner:4", newGuard, newDirectoryChanged));
    expect(await screen.findByRole("button", { name: /新会话团队/ })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("团队名称"), "新会话草稿");
    expect(newGuard).toHaveBeenLastCalledWith("new-owner:4", true, false);

    await act(async () => { pending.resolve({ ...team, id: "old-created-team", name: "旧会话团队" }); });
    expect(screen.getByLabelText("团队名称")).toHaveValue("新会话草稿");
    expect(screen.queryByRole("heading", { name: "旧会话团队" })).not.toBeInTheDocument();
    expect(oldDirectoryChanged).not.toHaveBeenCalled();
    expect(newDirectoryChanged).not.toHaveBeenCalled();
    expect(newGuard).toHaveBeenLastCalledWith("new-owner:4", true, false);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("ignores a late 401 from the old owner after the same page receives a new session", async () => {
    const stale = deferred<Array<{ id: string; name: string; owner_id: string; created_at: string; member_count: number; my_role: string }>>();
    const oldPort = createPort({ listTeams: vi.fn(() => stale.promise) });
    const newPort = createPort({
      listTeams: vi.fn(async () => [{ id: "team-new", name: "新会话团队", owner_id: "new-owner", created_at: now, member_count: 1, my_role: "owner" }]),
    });
    const oldServices: WorkspaceServices = { ...createDemoServices(), teamManagement: oldPort };
    const newServices: WorkspaceServices = { ...createDemoServices(), teamManagement: newPort };
    const onUnauthorized = vi.fn();
    const renderPage = (services: WorkspaceServices, ownerId: string, session: number, scope: string) => (
      <TeamsPage
        services={services}
        userId={ownerId}
        sessionGeneration={session}
        guardScopeKey={scope}
        onUnauthorized={onUnauthorized}
        onBack={vi.fn()}
        onGuardChange={vi.fn()}
        onDirectoryChanged={vi.fn()}
      />
    );
    const view = render(renderPage(oldServices, "old-owner", 1, "old-owner:1"));
    await waitFor(() => expect(oldPort.listTeams).toHaveBeenCalledTimes(1));
    view.rerender(renderPage(newServices, "new-owner", 2, "new-owner:2"));
    expect(await screen.findByRole("button", { name: /新会话团队/ })).toBeInTheDocument();

    await act(async () => {
      stale.reject(new ApiError("http", "old session", 401));
      await Promise.resolve();
    });
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /新会话团队/ })).toBeInTheDocument();
  });
});
