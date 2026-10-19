import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { createDemoServices, type WorkspaceServices } from "../../shared/api/services";
import { ApiError } from "../../shared/api/errors";
import type { Series } from "../../shared/api/contracts";
import type { TeamDetail } from "../../shared/api/teamManagement";
import type { StylePromptChoice } from "../../shared/api/seriesManagement";
import type { TeamSeriesRecord, TeamSeriesServicePort } from "../../shared/api/teamSeries";
import { demoSeries } from "../series/demoSeries";
import { TeamSeriesPanel } from "./TeamSeriesPanel";

const now = "2026-10-19T12:00:00";
const team: TeamDetail = {
  id: "team-1", name: "创作组", owner_id: "owner", created_at: now, my_role: "member", my_permissions: [],
  members: [
    { user_id: "owner", username: "Owner", avatar_url: null, role: "owner", permissions: [], joined_at: now },
    { user_id: "member", username: "Member", avatar_url: null, role: "member", permissions: [], joined_at: now },
    { user_id: "target", username: "Target", avatar_url: null, role: "member", permissions: [], joined_at: now },
  ],
};
const row: TeamSeriesRecord = {
  id: "series-1", name: "北境来信", description: null, image_url: null, user_id: "creator", owner_name: "Creator",
  owner_avatar_url: null, claimed_by: null, claimed_by_username: "", claimed_by_avatar_url: null,
  created_at: now, updated_at: now, chapter_count: 0,
  asset_counts: { characters: 0, scenes: 0, props: 0, storyboard: 0 },
};

function port(record = row): TeamSeriesServicePort {
  return {
    listTeamSeries: vi.fn(async () => ({ items: [record], total: 1, page: 1, page_size: 10, total_pages: 1 })),
    createTeamSeries: vi.fn(async () => ({ id: "created", name: "新剧集", team_id: team.id, user_id: "member", style_prompt_id: null })),
    shareSeries: vi.fn(async () => ({ message: "已分享到团队「创作组」", team_id: team.id, team_name: team.name })),
    unshareSeries: vi.fn(async () => ({ message: "已移出团队，恢复为个人剧集" })),
    claimTeamSeries: vi.fn(async () => ({ message: "认领成功", claimed_by: "member", claimed_at: now })),
    unclaimTeamSeries: vi.fn(async () => ({ message: "已取消认领" })),
    transferTeamSeries: vi.fn(async (_teamId, _seriesId, userId) => ({ message: "已转交认领", claimed_by: userId })),
  };
}

function renderPanel(options: {
  actorId?: string;
  currentTeam?: TeamDetail;
  record?: TeamSeriesRecord;
  getSeries?: WorkspaceServices["getSeries"];
} = {}) {
  const api = port(options.record);
  const onWrite = vi.fn();
  const onOpenSeries = vi.fn();
  const services: WorkspaceServices = {
    ...createDemoServices(),
    ...(options.getSeries === undefined ? {} : { getSeries: options.getSeries }),
  };
  function Harness() {
    const [createDraft, setCreateDraft] = useState({ name: "", claim: false });
    const [transferTargets, setTransferTargets] = useState<Record<string, string>>({});
    return (
      <TeamSeriesPanel
        active
        actorId={options.actorId ?? "member"}
        createDraft={createDraft}
        locked={false}
        onCreateDraftChange={setCreateDraft}
        onOpenSeries={onOpenSeries}
        onSeriesChanged={vi.fn()}
        onTransferTargetChange={(seriesId, userId) => setTransferTargets((current) => ({ ...current, [seriesId]: userId }))}
        onUnauthorized={vi.fn()}
        onWrite={onWrite}
        pendingLabel={null}
        port={api}
        services={services}
        sessionGeneration={1}
        seriesRevision={0}
        team={options.currentTeam ?? team}
        teamId={team.id}
        transferTargets={transferTargets}
        unknownLabel={null}
      />
    );
  }
  render(<Harness />);
  return { api, onWrite, onOpenSeries };
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => {
    resolve = complete;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function stylePanel(services: WorkspaceServices, actorId = "member", sessionGeneration = 1, active = true) {
  return (
    <TeamSeriesPanel
      active={active}
      actorId={actorId}
      createDraft={{ name: "", claim: false }}
      locked={false}
      onCreateDraftChange={vi.fn()}
      onOpenSeries={vi.fn()}
      onSeriesChanged={vi.fn()}
      onTransferTargetChange={vi.fn()}
      onUnauthorized={vi.fn()}
      onWrite={vi.fn()}
      pendingLabel={null}
      port={port()}
      services={services}
      sessionGeneration={sessionGeneration}
      seriesRevision={0}
      team={team}
      teamId={team.id}
      transferTargets={{}}
      unknownLabel={null}
    />
  );
}

describe("TeamSeriesPanel", () => {
  it("keeps a deferred style-prompt read alive through its loading render and allows selection", async () => {
    const pending = deferred<StylePromptChoice[]>();
    let signal: AbortSignal | undefined;
    const services: WorkspaceServices = {
      ...createDemoServices(),
      listSeriesStylePrompts: vi.fn((requestSignal: AbortSignal) => {
        signal = requestSignal;
        return pending.promise;
      }),
    };
    render(stylePanel(services));

    expect(await screen.findByText("正在读取风格提示词…")).toBeInTheDocument();
    await waitFor(() => expect(services.listSeriesStylePrompts).toHaveBeenCalledTimes(1));
    expect(signal?.aborted).toBe(false);
    await act(async () => {
      pending.resolve([{ id: "style-comic", name: "漫画风", is_system: true, user_id: null }]);
    });
    expect(await screen.findByRole("option", { name: "漫画风" })).toBeInTheDocument();
    expect(signal?.aborted).toBe(false);
  });

  it("shows a deferred style-prompt failure without aborting or retrying it as a status change", async () => {
    const pending = deferred<StylePromptChoice[]>();
    const listSeriesStylePrompts = vi.fn(() => pending.promise);
    const services: WorkspaceServices = { ...createDemoServices(), listSeriesStylePrompts };
    render(stylePanel(services));

    expect(await screen.findByText("正在读取风格提示词…")).toBeInTheDocument();
    await act(async () => { pending.reject(new ApiError("network", "风格服务暂不可用")); });

    expect(await screen.findByText(/风格服务暂不可用/)).toBeInTheDocument();
    expect(listSeriesStylePrompts).toHaveBeenCalledTimes(1);
  });

  it("retires an old style read on scope change and ignores its late result", async () => {
    const oldRead = deferred<StylePromptChoice[]>();
    const newRead = deferred<StylePromptChoice[]>();
    let oldSignal: AbortSignal | undefined;
    const oldServices: WorkspaceServices = {
      ...createDemoServices(),
      listSeriesStylePrompts: vi.fn((signal: AbortSignal) => {
        oldSignal = signal;
        return oldRead.promise;
      }),
    };
    const newServices: WorkspaceServices = {
      ...oldServices,
      listSeriesStylePrompts: vi.fn(() => newRead.promise),
    };
    const view = render(stylePanel(oldServices));
    expect(await screen.findByText("正在读取风格提示词…")).toBeInTheDocument();

    view.rerender(stylePanel(newServices, "next-actor", 2));
    await waitFor(() => expect(newServices.listSeriesStylePrompts).toHaveBeenCalledTimes(1));
    expect(oldSignal?.aborted).toBe(true);
    await act(async () => {
      newRead.resolve([{ id: "new-style", name: "新会话风格", is_system: false, user_id: "next-actor" }]);
    });
    expect(await screen.findByRole("option", { name: "新会话风格" })).toBeInTheDocument();
    await act(async () => {
      oldRead.resolve([{ id: "old-style", name: "旧会话风格", is_system: true, user_id: null }]);
    });
    expect(screen.queryByRole("option", { name: "旧会话风格" })).not.toBeInTheDocument();
  });

  it("lets an ordinary member create with an explicit false claim and claim an unclaimed series", async () => {
    const user = userEvent.setup();
    const { api, onWrite } = renderPanel();
    await screen.findByRole("heading", { name: "北境来信" });

    await user.type(screen.getByLabelText("名称"), "新剧集");
    await user.click(screen.getByRole("button", { name: "创建团队剧集" }));
    expect(onWrite).toHaveBeenCalledTimes(1);
    const create = onWrite.mock.calls[0]?.[0];
    expect(create?.label).toBe("创建团队剧集");
    await create?.request(api, new AbortController().signal);
    expect(api.createTeamSeries).toHaveBeenCalledWith(team.id, { name: "新剧集", claim: false }, "member", expect.any(AbortSignal));

    vi.spyOn(window, "confirm").mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "认领剧集" }));
    expect(onWrite).toHaveBeenCalledTimes(2);
    const claim = onWrite.mock.calls[1]?.[0];
    expect(claim?.label).toBe("认领剧集");
    await claim?.request(api, new AbortController().signal);
    expect(api.claimTeamSeries).toHaveBeenCalledWith(team.id, row.id, "member", expect.any(AbortSignal));
  });

  it("does not grant an admin unclaim or transfer powers for another member's series", async () => {
    const adminTeam = { ...team, my_role: "admin" };
    const claimedRow = { ...row, user_id: "creator", claimed_by: "other", claimed_by_username: "Other" };
    renderPanel({ actorId: "admin", currentTeam: adminTeam, record: claimedRow });

    await screen.findByRole("heading", { name: "北境来信" });
    expect(screen.queryByRole("button", { name: "取消认领" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "转交认领" })).not.toBeInTheDocument();
  });

  it("offers direct assignment to an authored unclaimed series and keeps the selected recipient", async () => {
    const user = userEvent.setup();
    const authored = { ...row, user_id: "member" };
    const { api, onWrite } = renderPanel({ record: authored });
    await screen.findByRole("heading", { name: "北境来信" });
    const target = screen.getByLabelText("转交给成员");
    await user.selectOptions(target, "target");
    expect(target).toHaveValue("target");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "转交认领" }));
    const transfer = onWrite.mock.calls[0]?.[0];
    await transfer?.request(api, new AbortController().signal);
    expect(api.transferTeamSeries).toHaveBeenCalledWith(team.id, authored.id, "target", expect.any(AbortSignal));
    await waitFor(() => expect(target).toHaveValue("target"));
  });

  it("lets only the author revoke sharing after a lock-impact confirmation", async () => {
    const user = userEvent.setup();
    const authored = { ...row, user_id: "member" };
    const { api, onWrite } = renderPanel({ record: authored });
    await screen.findByRole("heading", { name: "北境来信" });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await user.click(screen.getByRole("button", { name: "撤回分享" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("清除此剧集所有章节编辑锁"));
    expect(onWrite).not.toHaveBeenCalled();

    vi.mocked(window.confirm).mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "撤回分享" }));
    const intent = onWrite.mock.calls[0]?.[0];
    expect(intent?.label).toBe("撤回剧集分享");
    await intent?.request(api, new AbortController().signal);
    expect(api.unshareSeries).toHaveBeenCalledWith(authored.id, expect.any(AbortSignal));
  });

  it("does not navigate when the real series detail denies entry", async () => {
    const user = userEvent.setup();
    const getSeries = vi.fn<NonNullable<WorkspaceServices["getSeries"]>>(async () => ({
      ...demoSeries[0]!,
      id: row.id,
      team_id: team.id,
      can_enter: false,
    } as Series));
    const { onOpenSeries } = renderPanel({ getSeries });
    await screen.findByRole("heading", { name: "北境来信" });

    await user.click(screen.getByRole("button", { name: "只读查看章节" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("当前账号暂时不能进入这部剧集");
    expect(onOpenSeries).not.toHaveBeenCalled();
    expect(getSeries).toHaveBeenCalledWith(row.id, expect.any(AbortSignal));
  });
});
