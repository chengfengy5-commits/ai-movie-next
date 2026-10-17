import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../shared/api/errors";
import type { Series } from "../../shared/api/contracts";
import type { WorkspaceServices } from "../../shared/api/services";
import type { UpdateSeriesInput } from "../../shared/api/seriesManagement";
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

function servicesWithSeriesManagement(
  rows: Series[],
  overrides: Partial<WorkspaceServices> = {},
): WorkspaceServices {
  const fallback = rows[0] ?? demoSeries[0]!;
  return {
    ...servicesWithSeries(rows),
    getSeries: async (seriesId) => {
      const found = rows.find((item) => item.id === seriesId);
      return found === undefined ? { ...fallback, id: seriesId } : { ...found };
    },
    createSeries: async (input) => ({
      ...fallback,
      id: "created-series",
      user_id: currentUserId,
      name: input.name,
      description: input.description,
      image_url: input.image_url,
      style_prompt_id: input.style_prompt_id,
      style_prompt_name: null,
      style_prompt_owner_name: null,
      team_id: null,
      team_name: null,
    }),
    updateSeries: async (seriesId, update) => ({
      ...fallback,
      ...update,
      id: seriesId,
      user_id: currentUserId,
    }),
    deleteSeries: async () => undefined,
    listSeriesStylePrompts: async () => [
      { id: "style-new", name: "新风格", is_system: true, user_id: null },
    ],
    ...overrides,
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

  it("does not fetch a team directory or series list while hidden, then loads both when activated", async () => {
    const user = userEvent.setup();
    const listMyTeams = vi.fn(async () => [teamAlpha]);
    const listSeries = vi.fn(async () => demoSeries);
    const services = { ...servicesWithSeries(demoSeries, false, listMyTeams), listSeries };
    const expectedTeamNames = [
      "长街灯火",
      "旧城新雪",
      "长街灯火 · 9",
      "旧城新雪 · 12",
      "雾港来信 · 13",
      "第七码头 · 14",
      "长街灯火 · 15",
      "山海有约 · 16",
      "雨夜来客 · 17",
      "旧城新雪 · 18",
      "雾港来信 · 19",
      "第七码头 · 20",
      "长街灯火 · 21",
      "山海有约 · 22",
      "雨夜来客 · 23",
      "旧城新雪 · 24",
    ];
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
    expect(listSeries).not.toHaveBeenCalled();
    expect(listMyTeams).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    expect(listSeries).not.toHaveBeenCalled();
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
    await screen.findAllByRole("article");
    expect(screen.getByRole("button", { name: "团队剧集" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("article").map((article) => (
      within(article).getByRole("heading", { level: 3 }).textContent
    ))).toEqual(expectedTeamNames);
    expect(screen.getByText("显示 16 / 16 部")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /加载更多/ })).not.toBeInTheDocument();
    expect(await screen.findByRole("option", { name: "共享工作室" })).toBeInTheDocument();
    expect(listSeries).toHaveBeenCalledTimes(1);
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

  it("does not read series while hidden and ignores a late list response after deactivation", async () => {
    const lateResponse = deferred<Series[]>();
    const lateSeries = { ...demoSeries[0]!, id: "late-hidden-series", name: "隐藏后迟到的剧集" };
    const activeSeries = { ...demoSeries[0]!, id: "active-series", name: "激活后读取的剧集" };
    const listSeries = vi.fn()
      .mockReturnValueOnce(lateResponse.promise)
      .mockResolvedValueOnce([activeSeries]);
    const services = servicesWithSeriesManagement([], { listSeries });
    const props = {
      active: false,
      services,
      userId: currentUserId,
      onUnauthorized: vi.fn(),
      onViewChapters: () => undefined,
      onViewAssets: () => undefined,
    };
    const view = render(<SeriesPage {...props} />);

    expect(listSeries).not.toHaveBeenCalled();
    view.rerender(<SeriesPage {...props} active={true} />);
    await waitFor(() => expect(listSeries).toHaveBeenCalledTimes(1));
    view.rerender(<SeriesPage {...props} active={false} />);
    await act(async () => {
      lateResponse.resolve([lateSeries]);
      await lateResponse.promise;
    });
    expect(screen.queryByText(lateSeries.name)).not.toBeInTheDocument();

    view.rerender(<SeriesPage {...props} active={true} />);
    expect(await screen.findByText(activeSeries.name)).toBeInTheDocument();
    expect(listSeries).toHaveBeenCalledTimes(2);
  });

  it("creates a personal series with trimmed metadata and refreshes the list", async () => {
    const user = userEvent.setup();
    const createSeries = vi.fn(async (input: {
      name: string;
      description: string;
      image_url: string | null;
      style_prompt_id: string | null;
    }) => ({
      ...demoSeries[0]!,
      id: "created-series",
      user_id: currentUserId,
      name: input.name,
      description: input.description,
      image_url: input.image_url,
      style_prompt_id: input.style_prompt_id,
    }));
    const listSeries = vi.fn(async () => []);
    const services = servicesWithSeriesManagement([], { createSeries, listSeries });
    render(
      <SeriesPage
        active={true}
        services={services}
        userId={currentUserId}
        onUnauthorized={vi.fn()}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );

    await screen.findByRole("heading", { name: "还没有剧集" });
    await user.click(screen.getByRole("button", { name: "创建剧集" }));
    await user.type(screen.getByLabelText("剧集名称"), "  新剧  ");
    await user.type(screen.getByLabelText("剧集简介"), "  一段简介  ");
    await user.selectOptions(screen.getByLabelText("风格提示词"), "style-new");
    await user.click(screen.getByRole("button", { name: "保存剧集" }));

    await screen.findByText("已创建《新剧》，正在刷新剧集列表。");
    expect(createSeries).toHaveBeenCalledTimes(1);
    expect(createSeries).toHaveBeenCalledWith({
      name: "新剧",
      description: "一段简介",
      image_url: null,
      style_prompt_id: "style-new",
    }, expect.any(AbortSignal));
    await waitFor(() => expect(listSeries).toHaveBeenCalledTimes(2));
  });

  it("keeps unknown style metadata when editing another field and only sends dirty values", async () => {
    const user = userEvent.setup();
    const row: Series = {
      ...demoSeries[0]!,
      id: "unknown-style-series",
      name: "当前剧名",
      image_url: "http://127.0.0.1:4175/uploads/current.webp",
      style_prompt_id: "foreign-style",
      style_prompt_name: "遗留风格",
      style_prompt_owner_name: "其他作者",
    };
    const updateSeries = vi.fn(async (seriesId: string, update: UpdateSeriesInput) => ({
      ...row,
      ...update,
      id: seriesId,
    }));
    render(
      <SeriesPage
        active={true}
        services={servicesWithSeriesManagement([row], {
          getSeries: async () => row,
          listSeriesStylePrompts: async () => { throw new ApiError("network", "目录不可用"); },
          updateSeries,
        })}
        userId={currentUserId}
        onUnauthorized={vi.fn()}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );

    const card = await screen.findByRole("article");
    await user.click(within(card).getByRole("button", { name: "编辑剧集：当前剧名" }));
    expect(await screen.findByRole("option", { name: "遗留风格 · 其他作者" })).toBeInTheDocument();
    expect(screen.getByText("风格提示词暂不可读取；当前绑定会保留，其他字段仍可编辑。")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("剧集名称"));
    await user.type(screen.getByLabelText("剧集名称"), "修改后的名称");
    await user.click(screen.getByRole("button", { name: "保存剧集" }));

    await screen.findByText("已保存《修改后的名称》的剧集信息，正在刷新剧集列表。");
    expect(updateSeries).toHaveBeenCalledTimes(1);
    expect(updateSeries).toHaveBeenCalledWith(row.id, { name: "修改后的名称" }, expect.any(AbortSignal));
  });

  it("sends explicit empty strings for clearing image and style, while a forbidden edit keeps the draft", async () => {
    const user = userEvent.setup();
    const row: Series = {
      ...demoSeries[0]!,
      id: "clear-metadata-series",
      image_url: "http://127.0.0.1:4175/uploads/current.webp",
      style_prompt_id: "style-old",
      style_prompt_name: "旧风格",
    };
    const updateSeries = vi.fn(async () => {
      throw new ApiError("http", "forbidden", 403, "没有编辑权限");
    });
    const onUnauthorized = vi.fn();
    render(
      <SeriesPage
        active={true}
        services={servicesWithSeriesManagement([row], {
          getSeries: async () => row,
          updateSeries,
        })}
        userId={currentUserId}
        onUnauthorized={onUnauthorized}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );

    const card = await screen.findByRole("article");
    await user.click(within(card).getByRole("button", { name: "编辑剧集：" + row.name }));
    await screen.findByRole("option", { name: "旧风格" });
    await user.click(screen.getByRole("button", { name: "清空封面" }));
    await user.selectOptions(screen.getByLabelText("风格提示词"), "");
    await user.click(screen.getByRole("button", { name: "保存剧集" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("没有编辑权限");
    expect(updateSeries).toHaveBeenCalledTimes(1);
    expect(updateSeries).toHaveBeenCalledWith(row.id, { image_url: "", style_prompt_id: "" }, expect.any(AbortSignal));
    expect(screen.getByLabelText("剧集名称")).toHaveValue(row.name);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("does not automatically retry an uncertain create and leaves its draft available for explicit list verification", async () => {
    const user = userEvent.setup();
    let createAttempts = 0;
    const listRows: Series[] = [];
    const createdSeries: Series = {
      ...demoSeries[0]!,
      id: "confirmed-retry-series",
      user_id: currentUserId,
      name: "结果未知的剧集",
    };
    const createSeries = vi.fn(async () => {
      createAttempts += 1;
      if (createAttempts === 1) {
        throw new ApiError("network", "connection lost");
      }
      listRows.unshift(createdSeries);
      return createdSeries;
    });
    const listSeries = vi.fn(async () => listRows);
    render(
      <SeriesPage
        active={true}
        services={servicesWithSeriesManagement([], { createSeries, listSeries })}
        userId={currentUserId}
        onUnauthorized={vi.fn()}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );

    await screen.findByRole("heading", { name: "还没有剧集" });
    await user.click(screen.getByRole("button", { name: "创建剧集" }));
    await user.type(screen.getByLabelText("剧集名称"), "结果未知的剧集");
    await user.click(screen.getByRole("button", { name: "保存剧集" }));
    expect(await screen.findByText(/提交结果暂时无法确认/)).toBeInTheDocument();
    expect(createSeries).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("剧集名称")).toHaveValue("结果未知的剧集");
    expect(screen.getByRole("button", { name: "确认重新提交" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "重新读取核对" }));
    expect(await screen.findByText(/请检查下方内容/)).toBeInTheDocument();
    expect(createSeries).toHaveBeenCalledTimes(1);
    expect(listSeries).toHaveBeenCalledTimes(2);
    const retryConfirmation = screen.getByRole("checkbox", { name: /我已核对重新读取的剧集列表/ });
    await user.click(retryConfirmation);
    await user.click(screen.getByRole("button", { name: "确认重新提交" }));
    expect(await screen.findByText("已创建《结果未知的剧集》，正在刷新剧集列表。")).toBeInTheDocument();
    expect(createSeries).toHaveBeenCalledTimes(2);
  });

  it("lets the server decide team deletion permission without granting restricted edit access", async () => {
    const user = userEvent.setup();
    const row = {
      ...demoSeries[0]!,
      id: "restricted-team-series",
      name: "受限团队剧集",
      user_id: "another-owner",
      team_id: "team-alpha",
      team_name: "共享工作室",
      can_enter: false,
    };
    const deleteSeries = vi.fn(async () => {
      throw new ApiError("http", "forbidden", 403, "只有团队管理员可以删除");
    });
    render(
      <SeriesPage
        active={true}
        services={servicesWithSeriesManagement([row], { deleteSeries })}
        userId={currentUserId}
        onUnauthorized={vi.fn()}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );

    const card = await screen.findByRole("article");
    expect(within(card).queryByRole("button", { name: "编辑剧集：受限团队剧集" })).not.toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: /只读查看章节/ })).not.toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "删除剧集：受限团队剧集" }));
    await user.type(screen.getByLabelText("输入剧集名称以确认"), "受限团队剧集");
    await user.click(screen.getByRole("button", { name: "删除剧集" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("只有团队管理员可以删除");
    expect(deleteSeries).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading", { name: "受限团队剧集" })).toBeInTheDocument();
  });

  it("preserves an uncertain edit draft until a detail read and explicit retry confirmation", async () => {
    const user = userEvent.setup();
    const initial = { ...demoSeries[0]!, id: "uncertain-edit-series", name: "原剧名" };
    const rows = [initial];
    let serverRow = { ...initial };
    let updateAttempts = 0;
    const updateSeries = vi.fn(async (_seriesId: string, update: UpdateSeriesInput) => {
      updateAttempts += 1;
      if (updateAttempts === 1) {
        throw new ApiError("network", "connection lost");
      }
      serverRow = { ...serverRow, ...update };
      rows[0] = serverRow;
      return serverRow;
    });
    const getSeries = vi.fn(async () => ({ ...serverRow }));
    render(
      <SeriesPage
        active={true}
        services={servicesWithSeriesManagement(rows, { getSeries, updateSeries })}
        userId={currentUserId}
        onUnauthorized={vi.fn()}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );

    const card = await screen.findByRole("article");
    await user.click(within(card).getByRole("button", { name: "编辑剧集：原剧名" }));
    await screen.findByDisplayValue("原剧名");
    await user.clear(screen.getByLabelText("剧集名称"));
    await user.type(screen.getByLabelText("剧集名称"), "保留的编辑草稿");
    await user.click(screen.getByRole("button", { name: "保存剧集" }));
    expect(await screen.findByText(/提交结果暂时无法确认/)).toBeInTheDocument();
    expect(screen.getByLabelText("剧集名称")).toHaveValue("保留的编辑草稿");
    expect(updateSeries).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "确认重新提交" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "重新读取核对" }));
    expect(await within(screen.getByRole("dialog")).findByText("原剧名")).toBeInTheDocument();
    expect(getSeries).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText("剧集名称")).toHaveValue("保留的编辑草稿");
    await user.click(screen.getByRole("checkbox", { name: /我已核对服务器详情/ }));
    await user.click(screen.getByRole("button", { name: "确认重新提交" }));

    expect(await screen.findByText("已保存《保留的编辑草稿》的剧集信息，正在刷新剧集列表。")).toBeInTheDocument();
    expect(updateSeries).toHaveBeenCalledTimes(2);
    expect(updateSeries).toHaveBeenLastCalledWith(initial.id, { name: "保留的编辑草稿" }, expect.any(AbortSignal));
  });

  it("requires exact-name confirmation for deletion and retains the series after a permission rejection", async () => {
    const user = userEvent.setup();
    const row = { ...demoSeries[0]!, id: "delete-series", name: "待删除剧集" };
    const deleteSeries = vi.fn(async () => {
      throw new ApiError("http", "forbidden", 403, "团队负责人无删除权限");
    });
    const onUnauthorized = vi.fn();
    render(
      <SeriesPage
        active={true}
        services={servicesWithSeriesManagement([row], { deleteSeries })}
        userId={currentUserId}
        onUnauthorized={onUnauthorized}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );

    const card = await screen.findByRole("article");
    await user.click(within(card).getByRole("button", { name: "删除剧集：待删除剧集" }));
    const confirm = screen.getByLabelText("输入剧集名称以确认");
    const remove = screen.getByRole("button", { name: "删除剧集" });
    expect(remove).toBeDisabled();
    await user.type(confirm, "待删除");
    expect(remove).toBeDisabled();
    await user.clear(confirm);
    await user.type(confirm, "待删除剧集");
    expect(remove).toBeEnabled();
    await user.click(remove);

    expect(await screen.findByRole("alert")).toHaveTextContent("团队负责人无删除权限");
    expect(deleteSeries).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading", { name: "待删除剧集" })).toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });


  it("requires a fresh detail read and explicit confirmation before retrying an uncertain delete", async () => {
    const user = userEvent.setup();
    const row = { ...demoSeries[0]!, id: "uncertain-delete-series", name: "待核对删除剧集" };
    const rows = [row];
    let deleteAttempts = 0;
    const deleteSeries = vi.fn(async () => {
      deleteAttempts += 1;
      if (deleteAttempts === 1) {
        throw new ApiError("network", "connection lost");
      }
      rows.splice(0, 1);
    });
    const getSeries = vi.fn(async () => row);
    render(
      <SeriesPage
        active={true}
        services={servicesWithSeriesManagement(rows, { deleteSeries, getSeries })}
        userId={currentUserId}
        onUnauthorized={vi.fn()}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );

    const card = await screen.findByRole("article");
    await user.click(within(card).getByRole("button", { name: "删除剧集：待核对删除剧集" }));
    await user.type(screen.getByLabelText("输入剧集名称以确认"), "待核对删除剧集");
    await user.click(screen.getByRole("button", { name: "删除剧集" }));
    expect(await screen.findByText(/删除结果暂时无法确认/)).toBeInTheDocument();
    expect(deleteSeries).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "确认再次删除" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "重新读取详情核对" }));
    expect(await screen.findByText(/重新读取确认剧集仍存在/)).toBeInTheDocument();
    expect(getSeries).toHaveBeenCalledTimes(1);
    const retryConfirmation = screen.getByRole("checkbox", { name: /我已核对剧集仍存在/ });
    await user.click(retryConfirmation);
    await user.click(screen.getByRole("button", { name: "确认再次删除" }));
    expect(deleteSeries).toHaveBeenCalledTimes(2);
    expect(await screen.findByRole("heading", { name: "还没有剧集" })).toBeInTheDocument();
  });

  it("closes an edit dialog and ignores its late detail response when the hidden page becomes inactive", async () => {
    const user = userEvent.setup();
    const row = { ...demoSeries[0]!, id: "late-detail-series" };
    const detail = deferred<Series>();
    const getSeries = vi.fn(() => detail.promise);
    const services = servicesWithSeriesManagement([row], { getSeries });
    const props = {
      active: true,
      services,
      userId: currentUserId,
      onUnauthorized: vi.fn(),
      onViewChapters: () => undefined,
      onViewAssets: () => undefined,
    };
    const view = render(<SeriesPage {...props} />);
    const card = await screen.findByRole("article");
    await user.click(within(card).getByRole("button", { name: "编辑剧集：" + row.name }));
    await waitFor(() => expect(getSeries).toHaveBeenCalledTimes(1));
    view.rerender(<SeriesPage {...props} active={false} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await act(async () => {
      detail.resolve(row);
      await detail.promise;
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(props.onUnauthorized).not.toHaveBeenCalled();
  });

  it("shows JSON import and per-series export when the exchange service port is available", async () => {
    const user = userEvent.setup();
    const row = { ...demoSeries[0]!, id: "exchange-series", name: "可交换剧集", can_enter: true };
    const exportSeries = vi.fn(async () => ({ version: 1, chapters: [] }));
    const importSeries = vi.fn(async () => ({ id: "imported", name: "导入剧集", message: "ok" }));
    const service = Object.assign(servicesWithSeriesManagement([row]), { exportSeries, importSeries });
    render(
      <SeriesPage
        active={true}
        services={service}
        userId={currentUserId}
        onUnauthorized={vi.fn()}
        onViewChapters={() => undefined}
        onViewAssets={() => undefined}
      />,
    );

    const card = await screen.findByRole("article");
    expect(screen.getByRole("button", { name: "导入剧集 / 章节" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "导出剧集 JSON：可交换剧集" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "导入剧集 / 章节" }));
    expect(screen.getByRole("dialog", { name: "导入剧集或章节" })).toBeInTheDocument();
  });

  it("reloads the hidden team directory after a confirmed management revision and clears a removed selection", async () => {
    const user = userEvent.setup();
    const listMyTeams = vi.fn()
      .mockResolvedValueOnce([teamAlpha, teamBeta])
      .mockResolvedValueOnce([teamBeta]);
    const rows = [...manyTeamSeries(1, "team-alpha"), ...manyTeamSeries(1, "team-beta")];
    const services = servicesWithSeries(rows, false, listMyTeams);
    const props = {
      active: true,
      services,
      userId: currentUserId,
      onUnauthorized: vi.fn(),
      onViewChapters: () => undefined,
      onViewAssets: () => undefined,
    };
    const view = render(<SeriesPage {...props} teamDirectoryRevision={0} />);
    await screen.findAllByRole("article");
    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    await screen.findByRole("option", { name: "共享工作室" });
    await user.selectOptions(screen.getByLabelText("筛选团队"), teamAlpha.id);
    expect(screen.getByLabelText("筛选团队")).toHaveValue(teamAlpha.id);

    view.rerender(<SeriesPage {...props} teamDirectoryRevision={1} />);
    await waitFor(() => expect(listMyTeams).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByLabelText("筛选团队")).toHaveValue(""));
    expect(screen.getByRole("status")).toHaveTextContent("团队名单已变化");
  });

});
