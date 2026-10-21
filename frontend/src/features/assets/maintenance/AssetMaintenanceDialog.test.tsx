import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Character } from "../../../shared/api/contracts";
import { ApiError } from "../../../shared/api/errors";
import type { AssetMaintenanceServicePort, AssetDuplicateList, AssetIgnoredDuplicateList } from "../../../shared/api/assetMaintenance";
import type { WorkspaceServices } from "../../../shared/api/services";
import { AssetMaintenanceDialog } from "./AssetMaintenanceDialog";

const seriesId = "series-maintenance";

function character(id: string, name: string): Character {
  return {
    id,
    series_id: seriesId,
    name,
    gender: "",
    age: "",
    role: "",
    appearance: "",
    description: "",
    image_url: null,
    audio_url: null,
    voice_ref: null,
    aliases: [],
    canonical_key: null,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-01T00:00:00",
  };
}

const directory = [character("character-a", "林简"), character("character-b", "小林")];

function duplicates(groups: AssetDuplicateList["groups"] = [{
  index: 0,
  items: [
    { id: "character-a", name: "林简", description: "旧城档案员", image_url: null, ref_count: 3, aliases: ["小林"], canonical_key: null },
    { id: "character-b", name: "小林", description: "常被叫作小林", image_url: null, ref_count: 1, aliases: [], canonical_key: null },
  ],
}]): AssetDuplicateList {
  return { type: "character", total: 8, ignored_total: 0, groups };
}

function ignored(items: AssetIgnoredDuplicateList["items"] = []): AssetIgnoredDuplicateList {
  return { type: "character", total: items.length, items };
}

function makePort(overrides: Partial<AssetMaintenanceServicePort> = {}): AssetMaintenanceServicePort {
  return {
    checkOrphans: vi.fn(async () => ({ orphans: [] })),
    listDuplicates: vi.fn(async () => duplicates()),
    setDuplicateIgnored: vi.fn(async (_seriesId, input) => input.ignored
      ? { ignored: true as const, pairs_added: 1, pairs_total: 1 }
      : { ignored: false as const, pairs_removed: 1, pairs_total: 1 }),
    listIgnoredDuplicates: vi.fn(async () => ignored()),
    mergeAssets: vi.fn(async () => ({
      message: "合并完成",
      keep_id: "character-a",
      merged_count: 1,
      redirected_frames: 3,
      fused_updated: 2,
      aliases_added: 1,
      dropped_images: [{ id: "character-b", name: "小林" }],
      aliases: ["小林"],
    })),
    ...overrides,
  };
}

function makeServices(port: AssetMaintenanceServicePort, overrides: Partial<WorkspaceServices> = {}): WorkspaceServices {
  return {
    mode: "api",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    assetMaintenance: port,
    restore: async () => null,
    login: async () => { throw new Error("Not used in this test."); },
    listSeries: async () => [],
    listMyTeams: async () => [],
    listChapters: async () => [],
    listStoryboardAssets: async () => [],
    listCharacters: vi.fn(async () => directory),
    listScenes: async () => [],
    listProps: async () => [],
    getPersonalProductionNotes: async () => { throw new Error("Not used in this test."); },
    getPersonalRoughCut: async () => { throw new Error("Not used in this test."); },
    listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    logout: vi.fn(),
    ...overrides,
  };
}

function renderDialog(
  port = makePort(),
  options: {
    directoryItems?: Character[];
    directoryEpoch?: number;
    isCurrent?: () => boolean;
    isDirectoryCurrent?: (items: Character[], directoryEpoch: number) => boolean;
    services?: WorkspaceServices;
  } = {},
) {
  const services = options.services ?? makeServices(port);
  const onClose = vi.fn();
  const onUnauthorized = vi.fn();
  const onDirectoryUpdated = vi.fn();
  const onDirectoryNeedsRefresh = vi.fn(() => 1);
  const onOrphanActionsLockedChange = vi.fn();
  const onLocateAsset = vi.fn();
  const onOpenDelete = vi.fn();
  const view = render(
    <AssetMaintenanceDialog
      apiBaseUrl={services.apiBaseUrl}
      category="characters"
      directoryItems={options.directoryItems ?? directory}
      directoryEpoch={options.directoryEpoch ?? 1}
      isCurrent={options.isCurrent ?? (() => true)}
      isDirectoryCurrent={(items, directoryEpoch) => options.isDirectoryCurrent?.(items as Character[], directoryEpoch) ?? true}
      onClose={onClose}
      onDirectoryNeedsRefresh={onDirectoryNeedsRefresh}
      onDirectoryUpdated={onDirectoryUpdated}
      onOrphanActionsLockedChange={onOrphanActionsLockedChange}
      onLocateAsset={onLocateAsset}
      onOpenDelete={onOpenDelete}
      onUnauthorized={onUnauthorized}
      port={port}
      seriesId={seriesId}
      seriesName="旧城档案"
      services={services}
      userId="user-a"
    />,
  );
  return { ...view, services, onClose, onUnauthorized, onDirectoryUpdated, onDirectoryNeedsRefresh, onOrphanActionsLockedChange, onLocateAsset, onOpenDelete };
}

async function chooseBothItems(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  const boxes = await screen.findAllByRole("checkbox", { name: "纳入本次操作" });
  await user.click(boxes[0]!);
  await user.click(boxes[1]!);
}

describe("asset maintenance dialog", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("checks the complete category and only offers current, confirmed orphan IDs for existing deletion", async () => {
    const user = userEvent.setup();
    const port = makePort({
      checkOrphans: vi.fn(async () => ({ orphans: [
        { type: "character" as const, id: "character-a" },
        { type: "character" as const, id: "foreign-id" },
        { type: "prop" as const, id: "character-a" },
      ] })),
    });
    const callbacks = renderDialog(port);
    await screen.findByText("服务端识别的疑似重复组");
    await user.click(screen.getByRole("tab", { name: "孤立项" }));
    await user.click(screen.getByRole("button", { name: "检查孤立项" }));

    expect(await screen.findByText("已确认 1 项当前目录素材没有被已保存章节引用。检查结果仅代表本次读取时点。"))
      .toBeInTheDocument();
    expect(port.checkOrphans).toHaveBeenCalledExactlyOnceWith(seriesId, {
      items: [
        { type: "character", id: "character-a" },
        { type: "character", id: "character-b" },
      ],
    }, expect.any(AbortSignal));
    expect(await screen.findByText("服务返回了不属于当前目录的候选，已忽略这些结果。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开删除确认" }));
    expect(callbacks.onOpenDelete).toHaveBeenCalledExactlyOnceWith(directory[0], 1);
  });

  it("rejects an orphan response after the captured directory becomes stale", async () => {
    const user = userEvent.setup();
    let resolveCheck!: (value: { orphans: Array<{ type: "character"; id: string }> }) => void;
    const pending = new Promise<{ orphans: Array<{ type: "character"; id: string }> }>((resolve) => {
      resolveCheck = resolve;
    });
    const port = makePort({ checkOrphans: vi.fn(() => pending) });
    let directoryIsCurrent = true;
    const callbacks = renderDialog(port, { isDirectoryCurrent: () => directoryIsCurrent });
    await screen.findByText("旧城档案员");
    await user.click(screen.getByRole("tab", { name: "孤立项" }));
    await user.click(screen.getByRole("button", { name: "检查孤立项" }));
    await waitFor(() => expect(port.checkOrphans).toHaveBeenCalledTimes(1));

    directoryIsCurrent = false;
    await act(async () => {
      resolveCheck({ orphans: [{ type: "character", id: "character-a" }] });
      await pending;
    });

    expect(await screen.findByText("目录已更新，旧检查结果不能用于删除。请重新读取后再检查。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "打开删除确认" })).not.toBeInTheDocument();
    expect(callbacks.onOpenDelete).not.toHaveBeenCalled();
  });

  it("keeps an orphan check failure distinct from a confirmed empty result", async () => {
    const user = userEvent.setup();
    const port = makePort({
      checkOrphans: vi.fn()
        .mockRejectedValueOnce(new ApiError("network", "service unavailable"))
        .mockResolvedValueOnce({ orphans: [] }),
    });
    renderDialog(port);
    await screen.findByText("林简");
    await user.click(screen.getByRole("tab", { name: "孤立项" }));
    await user.click(screen.getByRole("button", { name: "检查孤立项" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("无法连接素材维护服务");
    expect(screen.queryByText("已完成检查，未发现孤立素材。")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "检查孤立项" }));

    expect(await screen.findByText("已完成检查，未发现孤立素材。")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("confirms not-a-duplicate and restore writes, then uses the fresh server lists", async () => {
    const user = userEvent.setup();
    const ignoredPair = {
      id: "pair-a-b",
      asset_id_a: "character-a",
      asset_id_b: "character-b",
      name_a: "林简",
      name_b: "小林",
      image_a: null,
      image_b: null,
      created_at: null,
    };
    const port = makePort({
      listDuplicates: vi.fn()
        .mockResolvedValueOnce(duplicates())
        .mockResolvedValueOnce(duplicates([]))
        .mockResolvedValueOnce(duplicates()),
      listIgnoredDuplicates: vi.fn()
        .mockResolvedValueOnce(ignored())
        .mockResolvedValueOnce(ignored([ignoredPair]))
        .mockResolvedValueOnce(ignored()),
    });
    renderDialog(port);
    await screen.findByText("林简");
    await chooseBothItems(user);
    await user.click(screen.getByRole("button", { name: "标记所选不是重复" }));
    const confirmation = screen.getByRole("group", { name: "操作确认" });
    expect(within(confirmation).getByText(/将组成一组忽略组合/)).toBeInTheDocument();
    await user.click(within(confirmation).getByRole("button", { name: "确认标记不是重复" }));

    await screen.findByText("标记不是重复已提交，疑似重复和已忽略列表已重新读取。");
    expect(port.setDuplicateIgnored).toHaveBeenCalledExactlyOnceWith(seriesId, {
      type: "character",
      ids: ["character-a", "character-b"],
      ignored: true,
    }, expect.any(AbortSignal));
    expect(screen.getByText("本次涉及组合数：1。")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /已忽略/ }));
    await user.click(screen.getByRole("button", { name: "恢复检测" }));
    const restoreConfirmation = screen.getByRole("group", { name: "操作确认" });
    await user.click(within(restoreConfirmation).getByRole("button", { name: "确认恢复检测" }));

    await screen.findByText("恢复检测已提交，疑似重复和已忽略列表已重新读取。");
    expect(port.setDuplicateIgnored).toHaveBeenCalledTimes(2);
    expect(port.setDuplicateIgnored).toHaveBeenLastCalledWith(seriesId, {
      type: "character",
      ids: ["character-a", "character-b"],
      ignored: false,
    }, expect.any(AbortSignal));
    expect(screen.getByText("本次涉及组合数：1。")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /已忽略/ })).toHaveTextContent("已忽略 (0)");
  });

  it("limits one group action to fifty explicitly selected items", async () => {
    const user = userEvent.setup();
    const items = Array.from({ length: 51 }, (_, index) => ({
      id: `character-${index + 1}`,
      name: `角色${index + 1}`,
      description: "",
      image_url: null,
      ref_count: 0,
      aliases: [],
      canonical_key: null,
    }));
    const port = makePort({
      listDuplicates: vi.fn(async () => ({
        type: "character" as const,
        total: items.length,
        ignored_total: 0,
        groups: [{ index: 0, items }],
      })),
    });
    renderDialog(port);
    const boxes = await screen.findAllByRole("checkbox", { name: "纳入本次操作" });
    for (const box of boxes.slice(0, 50)) await user.click(box);

    expect(boxes[49]).toBeChecked();
    expect(boxes[50]).toBeDisabled();
    expect(screen.getByRole("button", { name: "标记所选不是重复" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "标记所选不是重复" }));
    expect(screen.getByRole("group", { name: "操作确认" })).toHaveTextContent("以下 50 项将组成一组忽略组合");
    expect(port.setDuplicateIgnored).not.toHaveBeenCalled();
  }, 15_000);

  it("sends at most one merge when confirmation is activated twice while the write is pending", async () => {
    const user = userEvent.setup();
    let resolveMerge!: (value: Awaited<ReturnType<AssetMaintenanceServicePort["mergeAssets"]>>) => void;
    const pending = new Promise<Awaited<ReturnType<AssetMaintenanceServicePort["mergeAssets"]>>>((resolve) => {
      resolveMerge = resolve;
    });
    const mergeAssets = vi.fn(() => pending);
    const port = makePort({ mergeAssets });
    renderDialog(port);
    await screen.findByText("林简");
    await chooseBothItems(user);
    await user.click(screen.getAllByRole("radio", { name: "保留此项" })[0]!);
    await user.click(screen.getByRole("button", { name: "合并所选素材" }));
    const confirmButton = screen.getByRole("button", { name: "确认合并这些素材" });

    fireEvent.click(confirmButton);
    fireEvent.click(confirmButton);
    expect(mergeAssets).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveMerge({
        message: "合并完成",
        keep_id: "character-a",
        merged_count: 1,
        redirected_frames: 3,
        fused_updated: 2,
        aliases_added: 1,
        dropped_images: [{ id: "character-b", name: "小林" }],
        aliases: ["小林"],
      });
      await pending;
    });
    expect(await screen.findByText("合并已提交，素材目录和维护结果已重新读取。")).toBeInTheDocument();
  });

  it("requires an explicit retained item and confirmation, then shows the real merge receipt and directory reread", async () => {
    const user = userEvent.setup();
    const kept = character("character-a", "林简");
    const port = makePort({ listDuplicates: vi.fn().mockResolvedValueOnce(duplicates()).mockResolvedValueOnce(duplicates([])) });
    const listCharacters = vi.fn(async () => [kept]);
    const services = makeServices(port, { listCharacters });
    const callbacks = renderDialog(port, { services });
    await screen.findByText("林简");
    await chooseBothItems(user);
    const keepChoices = screen.getAllByRole("radio", { name: "保留此项" });
    await user.click(keepChoices[0]!);
    await user.click(screen.getByRole("button", { name: "合并所选素材" }));
    const confirmation = screen.getByRole("group", { name: "操作确认" });
    expect(within(confirmation).getByText(/全剧引用与融合成员会改指向保留项/)).toBeInTheDocument();
    expect(within(confirmation).getByText(/多张图片不会全部保留/)).toBeInTheDocument();
    await user.click(within(confirmation).getByRole("button", { name: "确认合并这些素材" }));

    expect(port.mergeAssets).toHaveBeenCalledExactlyOnceWith(seriesId, {
      type: "character",
      keep_id: "character-a",
      merge_ids: ["character-b"],
    }, expect.any(AbortSignal));
    expect(callbacks.onDirectoryNeedsRefresh).toHaveBeenCalled();
    await screen.findByText(/合并已提交，素材目录和维护结果已重新读取/);
    expect(screen.getByText(/改写 3 个镜头引用；更新 2 个融合成员/)).toBeInTheDocument();
    expect(screen.getByText("未保留图片：小林")).toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledExactlyOnceWith(seriesId, expect.any(AbortSignal));
    expect(callbacks.onDirectoryUpdated).toHaveBeenCalledExactlyOnceWith({ category: "characters", items: [kept] }, 1);
  });

  it("keeps an unknown merge frozen through reconciliation and never resends it", async () => {
    const user = userEvent.setup();
    const port = makePort({
      mergeAssets: vi.fn(async () => { throw new ApiError("network", "connection lost"); }),
    });
    renderDialog(port);
    await screen.findByText("林简");
    await chooseBothItems(user);
    await user.click(screen.getAllByRole("radio", { name: "保留此项" })[0]!);
    await user.click(screen.getByRole("button", { name: "合并所选素材" }));
    await user.click(screen.getByRole("button", { name: "确认合并这些素材" }));
    expect(await screen.findByText(/合并请求结果未知/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "仅重新读取核对" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "关闭素材整理窗口" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "仅重新读取核对" }));
    expect(await screen.findByText(/无法证明先前请求的写入结果/)).toBeInTheDocument();
    expect(port.mergeAssets).toHaveBeenCalledTimes(1);
    expect(port.listDuplicates).toHaveBeenCalledTimes(2);
  });

  it("reports a committed merge when the readback fails and only retries reads", async () => {
    const user = userEvent.setup();
    const port = makePort({
      listDuplicates: vi.fn()
        .mockResolvedValueOnce(duplicates())
        .mockRejectedValueOnce(new ApiError("network", "readback unavailable"))
        .mockResolvedValueOnce(duplicates([])),
    });
    renderDialog(port);
    await screen.findByText("林简");
    await chooseBothItems(user);
    await user.click(screen.getAllByRole("radio", { name: "保留此项" })[0]!);
    await user.click(screen.getByRole("button", { name: "合并所选素材" }));
    await user.click(screen.getByRole("button", { name: "确认合并这些素材" }));

    expect(await screen.findByText(/合并已提交，但重新读取失败/)).toBeInTheDocument();
    expect(screen.getByText(/保留素材编号：character-a/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "仅重新读取目录和维护结果" }));
    await screen.findByText(/写入已提交，目录和维护数据已重新读取/);
    expect(port.mergeAssets).toHaveBeenCalledTimes(1);
    expect(port.listDuplicates).toHaveBeenCalledTimes(3);
  });

  it("clears a committed receipt after a later access loss while retaining the commit fact", async () => {
    const user = userEvent.setup();
    const port = makePort({
      listDuplicates: vi.fn()
        .mockResolvedValueOnce(duplicates())
        .mockRejectedValueOnce(new ApiError("network", "readback unavailable"))
        .mockRejectedValueOnce(new ApiError("http", "forbidden", 403)),
    });
    renderDialog(port);
    await screen.findByText("林简");
    await chooseBothItems(user);
    await user.click(screen.getAllByRole("radio", { name: "保留此项" })[0]!);
    await user.click(screen.getByRole("button", { name: "合并所选素材" }));
    await user.click(screen.getByRole("button", { name: "确认合并这些素材" }));

    expect(await screen.findByText(/合并已提交，但重新读取失败/)).toBeInTheDocument();
    expect(screen.getByText(/保留素材编号：character-a/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "仅重新读取目录和维护结果" }));

    const notice = await screen.findByText(/合并已提交/);
    expect(notice.parentElement).toHaveTextContent(/访问状态已变化，请重新读取并确认当前素材目录/);
    expect(screen.queryByText(/保留素材编号：character-a/)).not.toBeInTheDocument();
    expect(screen.queryByText("未保留图片：小林")).not.toBeInTheDocument();
    expect(port.mergeAssets).toHaveBeenCalledTimes(1);
  });

  it("clears a committed receipt after a later 404 following successful readback", async () => {
    const user = userEvent.setup();
    const port = makePort({
      listDuplicates: vi.fn()
        .mockResolvedValueOnce(duplicates())
        .mockResolvedValueOnce(duplicates([]))
        .mockRejectedValueOnce(new ApiError("http", "missing", 404)),
    });
    renderDialog(port);
    await screen.findByText("林简");
    await chooseBothItems(user);
    await user.click(screen.getAllByRole("radio", { name: "保留此项" })[0]!);
    await user.click(screen.getByRole("button", { name: "合并所选素材" }));
    await user.click(screen.getByRole("button", { name: "确认合并这些素材" }));

    expect(await screen.findByText(/合并已提交，素材目录和维护结果已重新读取/)).toBeInTheDocument();
    expect(screen.getByText(/保留素材编号：character-a/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新读取" }));

    const notice = await screen.findByText(/已提交/);
    expect(notice.parentElement).toHaveTextContent(/访问状态已变化，请重新读取并确认当前素材目录/);
    expect(screen.queryByText(/保留素材编号：character-a/)).not.toBeInTheDocument();
    expect(screen.queryByText("未保留图片：小林")).not.toBeInTheDocument();
  });

  it("clears sensitive duplicate data after a 403 and does not treat the request as an empty result", async () => {
    const user = userEvent.setup();
    const port = makePort({
      checkOrphans: vi.fn(async () => { throw new ApiError("http", "forbidden", 403); }),
    });
    renderDialog(port);
    expect(await screen.findByText("旧城档案员")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "孤立项" }));
    await user.click(screen.getByRole("button", { name: "检查孤立项" }));
    expect(await screen.findByText("访问状态已变化，请重新读取并确认当前素材目录。")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "疑似重复" }));
    expect(await screen.findByText("访问状态已变化，请重新读取并确认当前素材目录。")).toBeInTheDocument();
    expect(screen.queryByText("林简")).not.toBeInTheDocument();
  });

  it("rejects a late unauthorized result after the same services object replaces its maintenance port", async () => {
    const pending = (() => {
      let reject!: (reason: unknown) => void;
      const promise = new Promise<AssetDuplicateList>((_resolve, fail) => { reject = fail; });
      return { promise, reject };
    })();
    const oldPort = makePort({ listDuplicates: vi.fn(() => pending.promise) });
    const newPort = makePort();
    const services = makeServices(oldPort);
    const callbacks = renderDialog(oldPort, { services });
    await waitFor(() => expect(oldPort.listDuplicates).toHaveBeenCalledTimes(1));
    Object.assign(services, { assetMaintenance: newPort });
    callbacks.rerender(
      <AssetMaintenanceDialog
        apiBaseUrl={services.apiBaseUrl}
        category="characters"
        directoryItems={directory}
        directoryEpoch={1}
        isCurrent={() => true}
        isDirectoryCurrent={() => true}
        onClose={callbacks.onClose}
        onDirectoryNeedsRefresh={callbacks.onDirectoryNeedsRefresh}
        onDirectoryUpdated={callbacks.onDirectoryUpdated}
        onOrphanActionsLockedChange={callbacks.onOrphanActionsLockedChange}
        onLocateAsset={callbacks.onLocateAsset}
        onOpenDelete={callbacks.onOpenDelete}
        onUnauthorized={callbacks.onUnauthorized}
        port={newPort}
        seriesId={seriesId}
        seriesName="旧城档案"
        services={services}
        userId="user-a"
      />,
    );

    await act(async () => {
      pending.reject(new ApiError("http", "late unauthorized", 401));
      await pending.promise.catch(() => undefined);
    });
    expect(callbacks.onUnauthorized).not.toHaveBeenCalled();
    expect(screen.queryByText("林简")).not.toBeInTheDocument();
  });
});
