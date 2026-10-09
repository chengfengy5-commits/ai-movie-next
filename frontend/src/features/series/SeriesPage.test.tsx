import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../shared/api/errors";
import type { Series } from "../../shared/api/contracts";
import type { WorkspaceServices } from "../../shared/api/services";
import type { MyTeam } from "../../shared/api/teams";
import { demoSeries } from "./demoSeries";
import { demoTeams } from "./demoTeams";
import { SeriesPage } from "./SeriesPage";

const currentUserId = "demo-user";

function servicesWithSeries(
  rows: Series[],
  currentUserIsSuperuser = false,
  listMyTeams: WorkspaceServices["listMyTeams"] = async () => demoTeams,
): WorkspaceServices {
  return {
    mode: "api",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    restore: async () => null,
    login: async () => ({
      id: currentUserIsSuperuser ? "superuser" : currentUserId,
      username: "当前用户",
      email: "user@example.invalid",
      is_superuser: currentUserIsSuperuser,
      membership_type: "free",
      membership_expires_at: null,
      avatar_url: null,
      bio: null,
      created_at: "2026-10-01T00:00:00",
    }),
    listSeries: async () => rows,
    listMyTeams,
    listChapters: async () => [],
    listStoryboardAssets: async () => [],
    listCharacters: async () => [],
    listScenes: async () => [],
    listProps: async () => [],
        getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
        getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
        listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    logout: () => undefined,
  };
}

function headings(): string[] {
  return screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent ?? "");
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

function manyTeamSeries(count: number, teamId = "team-alpha"): Series[] {
  return Array.from({ length: count }, (_, index) => ({
    ...demoSeries[index % demoSeries.length]!,
    id: `${teamId}-series-${index + 1}`,
    name: `${teamId} 剧集 ${index + 1}`,
    user_id: currentUserId,
    team_id: teamId,
    team_name: "共享工作室",
  }));
}

const teamAlpha: MyTeam = {
  id: "team-alpha",
  name: "共享工作室",
  owner_id: currentUserId,
  created_at: "2026-10-05T10:00:00",
  member_count: 3,
  my_role: "writer",
};

const teamBeta: MyTeam = { ...teamAlpha, id: "team-beta", name: "备用工作室" };

describe("SeriesPage list behavior", () => {
  afterEach(() => cleanup());

  it("keeps identical-timestamp API order and resets pagination when filters change", async () => {
    const user = userEvent.setup();
    const rows = demoSeries.map((series) => ({
      ...series,
      updated_at: "2026-10-01T00:00:00",
    }));
    render(<SeriesPage active={true} services={servicesWithSeries(rows)} userId={currentUserId} onUnauthorized={vi.fn()} onViewChapters={() => undefined} onViewAssets={() => undefined} />);

    await screen.findAllByRole("article");
    expect(headings()).toEqual(rows.slice(0, 20).map((series) => series.name));
    await user.click(screen.getByRole("button", { name: /加载更多/ }));
    expect(headings()).toEqual(rows.map((series) => series.name));

    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    expect(await screen.findAllByRole("option", { name: "拾光工作室" })).toHaveLength(2);
    const teamRows = rows.filter((series) => series.team_id !== null);
    expect(headings()).toEqual(teamRows.map((series) => series.name));
    expect(teamRows.some((series) => series.user_id === currentUserId)).toBe(true);

    await user.click(screen.getByRole("button", { name: /全部剧集/ }));
    expect(headings()).toEqual(rows.slice(0, 20).map((series) => series.name));
    expect(screen.getByRole("button", { name: /加载更多/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "我创建的" }));
    const ownedRows = rows.filter((series) => series.user_id === currentUserId);
    expect(headings()).toEqual(ownedRows.map((series) => series.name));
    expect(ownedRows.some((series) => series.team_id !== null)).toBe(true);
  });

  it("shows an empty filtered category without treating a shared list as empty", async () => {
    const user = userEvent.setup();
    render(<SeriesPage active={true} services={servicesWithSeries(demoSeries)} userId="unrelated-user" onUnauthorized={vi.fn()} onViewChapters={() => undefined} onViewAssets={() => undefined} />);

    await screen.findAllByRole("article");
    await user.click(screen.getByRole("button", { name: "我认领的" }));
    expect(await screen.findByRole("heading", { name: "这个分类暂时没有剧集" })).toBeInTheDocument();
    expect(screen.getByText("试试其他筛选，列表保持原始顺序。")).toBeInTheDocument();
  });

  it("keeps can_enter false cards restricted and renders untrusted fields as text", async () => {
    const row: Series = {
      ...demoSeries[0]!,
      id: "restricted-series",
      name: "<script>alert(1)</script>",
      image_url: "http://127.0.0.1:4175/missing-cover.webp",
      can_enter: false,
    };
    const { container } = render(
      <SeriesPage
        active={true}
        services={servicesWithSeries([row], true)}
        userId="superuser"
        onUnauthorized={vi.fn()}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );

    expect(await screen.findByRole("heading", { name: "<script>alert(1)</script>" })).toBeInTheDocument();
    expect(screen.getByText("当前账号受限")).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
    const brokenImage = container.querySelector("img");
    expect(brokenImage).not.toBeNull();
    fireEvent.error(brokenImage!);
    await waitFor(() => expect(container.querySelector("img")).toBeNull());
    expect(screen.getByText("HAO AI · SERIES")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it.each([
    [new ApiError("http", "forbidden", 403, "当前账号没有访问此列表的权限。"), "没有访问权限"],
    [new ApiError("http", "invalid", 422, "字段校验失败"), "列表请求未通过校验"],
    [new ApiError("http", "server error", 500), "服务暂时不可用"],
    [new ApiError("timeout", "timeout"), "请求超时"],
    [new ApiError("network", "offline"), "无法连接本地服务"],
    [new ApiError("invalid-response", "invalid response"), "服务返回的数据无法识别"],
  ])("presents %s errors with a working explicit retry", async (failure, heading) => {
    const user = userEvent.setup();
    const listSeries = vi.fn(async () => { throw failure; });
    const services = {
      ...servicesWithSeries([]),
      listSeries,
    } satisfies WorkspaceServices;
    render(<SeriesPage active={true} services={services} userId={currentUserId} onUnauthorized={vi.fn()} onViewChapters={() => undefined} onViewAssets={() => undefined} />);

    expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试读取" }));
    await waitFor(() => expect(listSeries).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "重试读取" })).toBeInTheDocument();
  });

  it("renders a genuinely empty server list separately from an empty category", async () => {
    render(<SeriesPage active={true} services={servicesWithSeries([])} userId={currentUserId} onUnauthorized={vi.fn()} onViewChapters={() => undefined} onViewAssets={() => undefined} />);

    expect(await screen.findByRole("heading", { name: "还没有剧集" })).toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  it("requests teams only in the visible team category and keeps options distinct by ID", async () => {
    const user = userEvent.setup();
    const listMyTeams = vi.fn(async () => demoTeams);
    render(
      <SeriesPage
        active={true}
        services={servicesWithSeries(demoSeries, false, listMyTeams)}
        userId={currentUserId}
        onUnauthorized={vi.fn()}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );

    await screen.findAllByRole("article");
    expect(listMyTeams).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "我认领的" }));
    expect(listMyTeams).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    expect(await screen.findAllByRole("option", { name: "拾光工作室" })).toHaveLength(2);
    expect(screen.getByRole("option", { name: "团队" })).toHaveValue("__proto__");
    expect(listMyTeams).toHaveBeenCalledTimes(1);

    await user.selectOptions(screen.getByLabelText("筛选团队"), "team-empty");
    expect(await screen.findByRole("heading", { name: "该团队暂无剧集" })).toBeInTheDocument();
    expect(screen.getByLabelText("筛选团队")).toHaveValue("team-empty");
    expect(listMyTeams).toHaveBeenCalledTimes(1);
  });

  it("shows an empty directory distinctly from a team with no series", async () => {
    const user = userEvent.setup();
    const listMyTeams = vi.fn(async (): Promise<MyTeam[]> => []);
    render(
      <SeriesPage
        active={true}
        services={servicesWithSeries(demoSeries, false, listMyTeams)}
        userId={currentUserId}
        onUnauthorized={vi.fn()}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );

    await screen.findAllByRole("article");
    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    expect(await screen.findByText("暂无所属团队。")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "全部团队" })).toBeInTheDocument();
    expect(screen.getAllByRole("article")).toHaveLength(16);
  });

  it("treats the literal all team ID as a team and keeps its name as text", async () => {
    const user = userEvent.setup();
    const hostileName = '<img src="x" onerror="alert(1)">';
    const allTeam: MyTeam = { ...teamAlpha, id: "all", name: hostileName };
    const rows = [
      { ...manyTeamSeries(1, "team-alpha")[0]!, name: "普通团队剧集" },
      { ...manyTeamSeries(1, "all")[0]!, name: "all ID 团队剧集" },
    ];
    const { container } = render(
      <SeriesPage
        active={true}
        services={servicesWithSeries(rows, false, async () => [teamAlpha, allTeam])}
        userId={currentUserId}
        onUnauthorized={vi.fn()}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );

    await screen.findAllByRole("article");
    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    const option = await screen.findByRole("option", { name: hostileName });
    expect(option.querySelector("img")).toBeNull();
    await user.selectOptions(screen.getByLabelText("筛选团队"), "all");

    expect(screen.getByLabelText("筛选团队")).toHaveValue("all");
    expect(headings()).toEqual(["all ID 团队剧集"]);
    expect(container.querySelector("option img")).toBeNull();
  });

  it("filters 45 rows before paging and preserves the team and 40-row view while hidden", async () => {
    const user = userEvent.setup();
    const listMyTeams = vi.fn(async () => [teamAlpha]);
    const services = servicesWithSeries(manyTeamSeries(45), false, listMyTeams);
    const props = {
      active: true,
      services,
      userId: currentUserId,
      onUnauthorized: vi.fn(),
      onViewChapters: () => undefined,
      onViewAssets: () => undefined,
    };
    const view = render(<SeriesPage {...props} />);

    await screen.findAllByRole("article");
    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    await screen.findByRole("option", { name: "共享工作室" });
    await user.selectOptions(screen.getByLabelText("筛选团队"), "team-alpha");
    expect(screen.getAllByRole("article")).toHaveLength(20);
    await user.click(screen.getByRole("button", { name: /加载更多/ }));
    expect(screen.getAllByRole("article")).toHaveLength(40);

    fireEvent.change(screen.getByLabelText("筛选团队"), { target: { value: "team-alpha" } });
    expect(screen.getAllByRole("article")).toHaveLength(40);
    view.rerender(<SeriesPage {...props} active={false} />);
    view.rerender(<SeriesPage {...props} active={true} />);
    expect(screen.getAllByRole("article")).toHaveLength(40);
    expect(screen.getByLabelText("筛选团队")).toHaveValue("team-alpha");
    expect(listMyTeams).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "我创建的" }));
    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    expect(screen.getAllByRole("article")).toHaveLength(20);
    expect(screen.getByLabelText("筛选团队")).toHaveValue("");
    expect(listMyTeams).toHaveBeenCalledTimes(1);
  });

  it("applies a directory refresh against the selection current when it settles", async () => {
    const user = userEvent.setup();
    const refresh = deferred<MyTeam[]>();
    const removal = deferred<MyTeam[]>();
    const listMyTeams = vi.fn()
      .mockResolvedValueOnce([teamAlpha, teamBeta])
      .mockReturnValueOnce(refresh.promise)
      .mockReturnValueOnce(removal.promise);
    const rows = [
      ...manyTeamSeries(22, "team-alpha"),
      ...manyTeamSeries(23, "team-beta"),
    ];
    render(
      <SeriesPage
        active={true}
        services={servicesWithSeries(rows, false, listMyTeams)}
        userId={currentUserId}
        onUnauthorized={vi.fn()}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );
    await screen.findAllByRole("article");
    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    await screen.findByRole("option", { name: "备用工作室" });
    await user.selectOptions(screen.getByLabelText("筛选团队"), "team-alpha");
    await user.click(screen.getByRole("button", { name: "重新读取团队" }));
    await user.selectOptions(screen.getByLabelText("筛选团队"), "team-beta");

    await act(async () => {
      refresh.resolve([teamAlpha, teamBeta]);
      await refresh.promise;
    });
    expect(screen.getByLabelText("筛选团队")).toHaveValue("team-beta");
    expect(headings()[0]).toBe("team-beta 剧集 1");
    expect(listMyTeams).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole("button", { name: "重新读取团队" }));
    expect(await screen.findByText("正在读取团队名单…")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("筛选团队"), "team-beta");
    await act(async () => {
      removal.resolve([teamAlpha]);
      await removal.promise;
    });
    expect(screen.getByLabelText("筛选团队")).toHaveValue("");
    expect(screen.getByRole("status")).toHaveTextContent("团队名单已变化");
    expect(screen.getAllByRole("article")).toHaveLength(20);
    expect(listMyTeams).toHaveBeenCalledTimes(3);
  });

  it.each(["success", "401"] as const)(
    "ignores a late %s from a refresh replaced by a newer directory request",
    async (lateResult) => {
      const user = userEvent.setup();
      const staleRefresh = deferred<MyTeam[]>();
      const onUnauthorized = vi.fn();
      const listMyTeams = vi.fn()
        .mockResolvedValueOnce([teamAlpha, teamBeta])
        .mockReturnValueOnce(staleRefresh.promise)
        .mockResolvedValueOnce([teamBeta]);
      const rows = [
        ...manyTeamSeries(1, "team-alpha"),
        ...manyTeamSeries(1, "team-beta"),
      ];
      render(
        <SeriesPage
          active={true}
          services={servicesWithSeries(rows, false, listMyTeams)}
          userId={currentUserId}
          onUnauthorized={onUnauthorized}
          onViewChapters={() => undefined}
          onViewAssets={() => undefined}
        />,
      );

      await screen.findAllByRole("article");
      await user.click(screen.getByRole("button", { name: "团队剧集" }));
      await screen.findByRole("option", { name: "备用工作室" });
      await user.selectOptions(screen.getByLabelText("筛选团队"), "team-beta");
      await user.click(screen.getByRole("button", { name: "重新读取团队" }));
      await user.click(screen.getByRole("button", { name: "重新读取团队" }));
      await waitFor(() => expect(listMyTeams).toHaveBeenCalledTimes(3));
      await waitFor(() => expect(screen.getByRole("option", { name: "备用工作室" })).toBeInTheDocument());

      await act(async () => {
        if (lateResult === "success") {
          staleRefresh.resolve([teamAlpha]);
          await staleRefresh.promise;
        } else {
          staleRefresh.reject(new ApiError("http", "expired", 401));
          await staleRefresh.promise.catch(() => undefined);
        }
      });

      expect(screen.getByLabelText("筛选团队")).toHaveValue("team-beta");
      expect(headings()).toEqual(["team-beta 剧集 1"]);
      expect(onUnauthorized).not.toHaveBeenCalled();
      expect(listMyTeams).toHaveBeenCalledTimes(3);
    },
  );

  it("keeps series and session after a directory failure until explicit retry", async () => {
    const user = userEvent.setup();
    const onUnauthorized = vi.fn();
    const listMyTeams = vi.fn()
      .mockRejectedValueOnce(new ApiError("http", "forbidden", 403, "团队目录不可用"))
      .mockResolvedValueOnce([teamAlpha]);
    render(
      <SeriesPage
        active={true}
        services={servicesWithSeries(demoSeries, false, listMyTeams)}
        userId={currentUserId}
        onUnauthorized={onUnauthorized}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );

    await screen.findAllByRole("article");
    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    expect(await screen.findByText("没有访问团队目录的权限")).toBeInTheDocument();
    expect(screen.getAllByRole("article")).toHaveLength(16);
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(listMyTeams).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "重新读取团队" }));
    expect(await screen.findByRole("option", { name: "共享工作室" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(listMyTeams).toHaveBeenCalledTimes(2);
  });

  it("does not fetch a team directory while the series list is hidden", async () => {
    const user = userEvent.setup();
    const listMyTeams = vi.fn(async () => [teamAlpha]);
    const services = servicesWithSeries(demoSeries, false, listMyTeams);
    const view = render(
      <SeriesPage
        active={false}
        services={services}
        userId={currentUserId}
        onUnauthorized={vi.fn()}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );
    await screen.findAllByRole("article");
    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    expect(listMyTeams).not.toHaveBeenCalled();
    view.rerender(
      <SeriesPage
        active={true}
        services={services}
        userId={currentUserId}
        onUnauthorized={vi.fn()}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );
    expect(await screen.findByRole("option", { name: "共享工作室" })).toBeInTheDocument();
    expect(listMyTeams).toHaveBeenCalledTimes(1);
  });

  it("preserves a team directory error across category changes and hidden returns", async () => {
    const user = userEvent.setup();
    const listMyTeams = vi.fn()
      .mockRejectedValueOnce(new ApiError("http", "forbidden", 403, "团队目录不可用"))
      .mockResolvedValueOnce([teamAlpha]);
    const services = servicesWithSeries(demoSeries, false, listMyTeams);
    const view = render(
      <SeriesPage
        active={true}
        services={services}
        userId={currentUserId}
        onUnauthorized={vi.fn()}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );
    await screen.findAllByRole("article");
    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    expect(await screen.findByText("没有访问团队目录的权限")).toBeInTheDocument();
    expect(listMyTeams).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: /全部剧集/ }));
    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    expect(screen.getByText("没有访问团队目录的权限")).toBeInTheDocument();
    expect(listMyTeams).toHaveBeenCalledTimes(1);

    view.rerender(
      <SeriesPage
        active={false}
        services={services}
        userId={currentUserId}
        onUnauthorized={vi.fn()}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );
    view.rerender(
      <SeriesPage
        active={true}
        services={services}
        userId={currentUserId}
        onUnauthorized={vi.fn()}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );
    expect(screen.getByText("没有访问团队目录的权限")).toBeInTheDocument();
    expect(listMyTeams).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "重新读取团队" }));
    expect(await screen.findByRole("option", { name: "共享工作室" })).toBeInTheDocument();
    expect(listMyTeams).toHaveBeenCalledTimes(2);
  });

  it.each(["user id", "services instance"] as const)(
    "hides the old directory, selection, error, and cards on a %s scope change immediately",
    async (scopeChange) => {
      const user = userEvent.setup();
      const oldTeam: MyTeam = { ...teamAlpha, id: "private-team", name: "旧账号团队名" };
      const nextTeam: MyTeam = { ...teamBeta, id: "next-team", name: "新作用域团队名" };
      const nextUserId = scopeChange === "user id" ? "another-user" : currentUserId;
      const oldSeries: Series = {
        ...demoSeries[0]!,
        name: "旧账号剧集",
        team_name: "旧卡片团队名",
        team_id: oldTeam.id,
      };
      const nextSeries: Series = {
        ...demoSeries[1]!,
        id: "new-scope-series",
        name: "新作用域剧集",
        user_id: nextUserId,
        team_id: nextTeam.id,
        team_name: nextTeam.name,
      };
      const freshSeries = deferred<Series[]>();
      const listMyTeams = vi.fn()
        .mockResolvedValue([nextTeam])
        .mockResolvedValueOnce([oldTeam])
        .mockRejectedValueOnce(new ApiError("http", "server error", 500));
      const firstServices = servicesWithSeries([oldSeries], false, listMyTeams);
      firstServices.listSeries = vi.fn()
        .mockResolvedValueOnce([oldSeries])
        .mockReturnValueOnce(freshSeries.promise);
      const view = render(
        <SeriesPage
          active={true}
          services={firstServices}
          userId={currentUserId}
          onUnauthorized={vi.fn()}
          onViewChapters={() => undefined}
          onViewAssets={() => undefined}
        />,
      );

      await screen.findAllByRole("article");
      await user.click(screen.getByRole("button", { name: "团队剧集" }));
      await screen.findByRole("option", { name: "旧账号团队名" });
      await user.selectOptions(screen.getByLabelText("筛选团队"), oldTeam.id);
      await user.click(screen.getByRole("button", { name: "重新读取团队" }));
      expect(await screen.findByText("团队目录暂时不可用")).toBeInTheDocument();
      expect(screen.getByLabelText("筛选团队")).toHaveValue(oldTeam.id);

      let nextServices = firstServices;
      if (scopeChange === "services instance") {
        nextServices = servicesWithSeries([nextSeries], false, async () => [nextTeam]);
        nextServices.listSeries = vi.fn(() => freshSeries.promise);
      }
      view.rerender(
        <SeriesPage
          active={true}
          services={nextServices}
          userId={nextUserId}
          onUnauthorized={vi.fn()}
          onViewChapters={() => undefined}
          onViewAssets={() => undefined}
        />,
      );

      expect(screen.queryByText("旧账号团队名")).not.toBeInTheDocument();
      expect(screen.queryByText("旧卡片团队名")).not.toBeInTheDocument();
      expect(screen.queryByText("旧账号剧集")).not.toBeInTheDocument();
      expect(screen.queryByText("团队目录暂时不可用")).not.toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent("正在读取剧集");

      await act(async () => {
        freshSeries.resolve([nextSeries]);
        await freshSeries.promise;
      });
      await user.click(screen.getByRole("button", { name: "团队剧集" }));
      expect(await screen.findByLabelText("筛选团队")).toHaveValue("");
      expect(await screen.findByRole("option", { name: "新作用域团队名" })).toBeInTheDocument();
      expect(screen.getByLabelText("筛选团队")).toHaveValue("");
      expect(screen.getByText("新作用域剧集")).toBeInTheDocument();
      expect(listMyTeams).toHaveBeenCalledTimes(scopeChange === "user id" ? 3 : 2);
    },
  );
});
