import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "../../shared/api/errors";
import { createDemoServices, type WorkspaceServices } from "../../shared/api/services";
import type { Series } from "../../shared/api/contracts";
import type { TeamSeriesServicePort } from "../../shared/api/teamSeries";
import type { MyTeam } from "../../shared/api/teams";
import { demoSeries } from "./demoSeries";
import { SeriesShareDialog } from "./SeriesShareDialog";

const actorId = "author-1";
const team: MyTeam = {
  id: "team-a",
  name: "剧组",
  owner_id: actorId,
  created_at: "2026-10-19T12:00:00",
  member_count: 2,
  my_role: "member",
};

function createPort(overrides: Partial<TeamSeriesServicePort> = {}): TeamSeriesServicePort {
  return {
    listTeamSeries: vi.fn(async () => ({ items: [], total: 0, page: 1, page_size: 10, total_pages: 1 })),
    createTeamSeries: vi.fn(async () => ({
      id: "created", name: "剧集", team_id: team.id, user_id: actorId, style_prompt_id: null,
    })),
    shareSeries: vi.fn(async () => ({ message: "已分享到团队「剧组」", team_id: team.id, team_name: team.name })),
    unshareSeries: vi.fn(async () => ({ message: "已移出团队，恢复为个人剧集" })),
    claimTeamSeries: vi.fn(async () => ({ message: "认领成功", claimed_by: actorId, claimed_at: "2026-10-19T12:00:00" })),
    unclaimTeamSeries: vi.fn(async () => ({ message: "该剧集尚未被认领" })),
    transferTeamSeries: vi.fn(async () => ({ message: "已转交认领", claimed_by: "member-2" })),
    ...overrides,
  };
}

function services(port: TeamSeriesServicePort, listSeries: WorkspaceServices["listSeries"] = vi.fn(async () => [])): WorkspaceServices {
  return {
    ...createDemoServices(),
    teamSeries: port,
    listMyTeams: vi.fn(async () => [team]),
    listSeries,
  };
}

function renderDialog(
  service: WorkspaceServices,
  series: Series = { ...demoSeries[0]!, id: "series-a", user_id: actorId, team_id: null, can_enter: false },
  overrides: Partial<Parameters<typeof SeriesShareDialog>[0]> = {},
) {
  const props: Parameters<typeof SeriesShareDialog>[0] = {
    active: true,
    actorId,
    sessionGeneration: 4,
    scopeKey: "author-1:4:series-share",
    series,
    services: service,
    onGuardChange: vi.fn(),
    onClose: vi.fn(),
    onUnauthorized: vi.fn(),
    onSeriesChanged: vi.fn(),
    onConfirmed: vi.fn(),
    ...overrides,
  };
  return { props, ...render(<SeriesShareDialog {...props} />) };
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

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SeriesShareDialog", () => {
  it("shares an author's restricted card with the explicit claim choice and releases the host guard after ACK", async () => {
    const user = userEvent.setup();
    const port = createPort();
    const service = services(port);
    const onGuardChange = vi.fn();
    const onConfirmed = vi.fn();
    const onSeriesChanged = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderDialog(service, undefined, { onGuardChange, onConfirmed, onSeriesChanged });

    await user.selectOptions(await screen.findByLabelText("分享给团队"), team.id);
    await user.click(screen.getByLabelText("同时由我认领"));
    await user.click(screen.getByRole("button", { name: "分享剧集" }));

    await waitFor(() => expect(onConfirmed).toHaveBeenCalledWith(`《${demoSeries[0]!.name}》已分享到团队「剧组」。`));
    expect(port.shareSeries).toHaveBeenCalledWith("series-a", team.id, true, expect.any(AbortSignal));
    expect(onSeriesChanged).toHaveBeenCalledWith(team.id, "series-a");
    expect(onGuardChange).toHaveBeenCalledWith("author-1:4:series-share", true, false);
    expect(onGuardChange).toHaveBeenLastCalledWith("author-1:4:series-share", false, false);
  });

  it("keeps the selected team and sends zero writes when share confirmation is canceled", async () => {
    const user = userEvent.setup();
    const port = createPort();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderDialog(services(port));

    await user.selectOptions(await screen.findByLabelText("分享给团队"), team.id);
    await user.click(screen.getByRole("button", { name: "分享剧集" }));

    expect(port.shareSeries).not.toHaveBeenCalled();
    expect(screen.getByLabelText("分享给团队")).toHaveValue(team.id);
  });

  it("keeps unknown ACK frozen, allows a read-only fact, and requires explicit abandon before retrying", async () => {
    const user = userEvent.setup();
    const port = createPort({ shareSeries: vi.fn(async () => { throw new ApiError("timeout", "late"); }) });
    const listSeries = vi.fn(async () => [{ ...demoSeries[0]!, id: "series-a", team_id: team.id, team_name: team.name }]);
    const service = services(port, listSeries);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderDialog(service);

    await user.selectOptions(await screen.findByLabelText("分享给团队"), team.id);
    await user.click(screen.getByRole("button", { name: "分享剧集" }));
    expect(await screen.findByLabelText("待核对的团队共享操作")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "分享剧集" })).toBeDisabled();
    expect(port.shareSeries).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "读取当前剧集事实" }));
    expect(await screen.findByText("当前剧集列表显示：团队共享至剧组。")).toBeInTheDocument();
    expect(port.shareSeries).toHaveBeenCalledTimes(1);
    expect(listSeries).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "明确放弃本地状态" }));
    await user.selectOptions(await screen.findByLabelText("分享给团队"), team.id);
    await user.click(screen.getByRole("button", { name: "分享剧集" }));
    await waitFor(() => expect(port.shareSeries).toHaveBeenCalledTimes(2));
  });

  it("allows an author to revoke sharing even when the current series snapshot denies entry", async () => {
    const user = userEvent.setup();
    const port = createPort();
    const service = services(port);
    const onConfirmed = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderDialog(service, {
      ...demoSeries[0]!,
      id: "series-a",
      user_id: actorId,
      team_id: team.id,
      team_name: team.name,
      can_enter: false,
    }, { onConfirmed });

    await user.click(screen.getByRole("button", { name: "撤回分享" }));

    await waitFor(() => expect(onConfirmed).toHaveBeenCalledWith(`《${demoSeries[0]!.name}》已撤回分享。`));
    expect(port.unshareSeries).toHaveBeenCalledWith("series-a", expect.any(AbortSignal));
  });

  it.each(["success", "401"] as const)(
    "drops a hidden prior-session %s without applying its ACK or logout", async (lateResult) => {
      const user = userEvent.setup();
      const pending = deferred<Awaited<ReturnType<TeamSeriesServicePort["shareSeries"]>>>();
      const oldPort = createPort({ shareSeries: vi.fn(() => pending.promise) });
      const oldServices = services(oldPort);
      const onUnauthorized = vi.fn();
      const onConfirmed = vi.fn();
      const onSeriesChanged = vi.fn();
      vi.spyOn(window, "confirm").mockReturnValue(true);
      const view = renderDialog(oldServices, undefined, { onUnauthorized, onConfirmed, onSeriesChanged });

      await user.selectOptions(await screen.findByLabelText("分享给团队"), team.id);
      await user.click(screen.getByRole("button", { name: "分享剧集" }));
      expect(oldPort.shareSeries).toHaveBeenCalledTimes(1);
      view.rerender(<SeriesShareDialog {...view.props} active={false} />);
      view.rerender(
        <SeriesShareDialog
          {...view.props}
          active={true}
          actorId="new-actor"
          scopeKey="new-actor:5:series-share"
          series={{ ...view.props.series, user_id: "new-actor" }}
          services={services(createPort())}
          sessionGeneration={5}
        />,
      );
      await screen.findByLabelText("分享给团队");

      await act(async () => {
        if (lateResult === "success") {
          pending.resolve({ message: "已分享到团队「剧组」", team_id: team.id, team_name: team.name });
        } else {
          pending.reject(new ApiError("http", "旧会话已过期", 401));
        }
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(onUnauthorized).not.toHaveBeenCalled();
      expect(onConfirmed).not.toHaveBeenCalled();
      expect(onSeriesChanged).not.toHaveBeenCalled();
      expect(screen.getByLabelText("分享给团队")).toHaveValue("");
    },
  );

  it.each(["success", "401", "403"] as const)(
    "retires a same-session share lease when only the team-series port changes before late %s",
    async (lateResult) => {
      const user = userEvent.setup();
      const oldResult = deferred<Awaited<ReturnType<TeamSeriesServicePort["shareSeries"]>>>();
      const newResult = deferred<Awaited<ReturnType<TeamSeriesServicePort["shareSeries"]>>>();
      let oldSignal: AbortSignal | undefined;
      const oldPort = createPort({
        shareSeries: vi.fn((_seriesId, _teamId, _claim, signal) => {
          oldSignal = signal;
          return oldResult.promise;
        }),
      });
      const service = services(oldPort);
      const onUnauthorized = vi.fn();
      const onConfirmed = vi.fn();
      const onSeriesChanged = vi.fn();
      vi.spyOn(window, "confirm").mockReturnValue(true);
      const view = renderDialog(service, undefined, { onUnauthorized, onConfirmed, onSeriesChanged });

      await user.selectOptions(await screen.findByLabelText("分享给团队"), team.id);
      await user.click(screen.getByRole("button", { name: "分享剧集" }));
      expect(oldPort.shareSeries).toHaveBeenCalledTimes(1);

      const nextPort = createPort({ shareSeries: vi.fn(() => newResult.promise) });
      Object.assign(service, { teamSeries: nextPort });
      view.rerender(<SeriesShareDialog {...view.props} services={service} />);
      await waitFor(() => expect(service.listMyTeams).toHaveBeenCalledTimes(2));
      expect(oldSignal?.aborted).toBe(true);
      await user.selectOptions(await screen.findByLabelText("分享给团队"), team.id);
      await user.click(screen.getByRole("button", { name: "分享剧集" }));
      expect(nextPort.shareSeries).toHaveBeenCalledTimes(1);
      expect(screen.getByRole("status")).toHaveTextContent("正在执行：分享剧集");

      await act(async () => {
        if (lateResult === "success") {
          oldResult.resolve({ message: "已分享到团队「旧端口」", team_id: "old-team", team_name: "旧端口" });
        } else {
          oldResult.reject(new ApiError("http", "旧端口结果", lateResult === "401" ? 401 : 403));
        }
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(onUnauthorized).not.toHaveBeenCalled();
      expect(onConfirmed).not.toHaveBeenCalled();
      expect(onSeriesChanged).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "分享剧集" })).toBeDisabled();

      await act(async () => {
        newResult.resolve({ message: "已分享到团队「剧组」", team_id: team.id, team_name: team.name });
      });
      await waitFor(() => expect(onConfirmed).toHaveBeenCalledTimes(1));
      expect(onSeriesChanged).toHaveBeenCalledTimes(1);
      expect(onUnauthorized).not.toHaveBeenCalled();
    },
  );
});
