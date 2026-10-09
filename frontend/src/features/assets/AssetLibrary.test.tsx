import { StrictMode, useLayoutEffect, useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter, Character, Prop, Scene } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { WorkspaceServices } from "../../shared/api/services";
import { demoSeries } from "../series/demoSeries";
import * as usagePanelModule from "./AssetFrameUsagePanel";
import type { AssetFrameUsagePanelProps } from "./AssetFrameUsagePanel";
import { AssetLibrary, type AssetNavigationIntent } from "./AssetLibrary";

const assetSearchRuntime = vi.hoisted(() => ({
  inputs: [] as Array<{ accessibleName: string; onChange: (event: unknown) => void }>,
  clearButtons: [] as Array<{ onClick: (event: unknown) => void }>,
}));

vi.mock("react/jsx-runtime", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react/jsx-runtime")>();
  const capture = (type: unknown, props: unknown) => {
    if (typeof props !== "object" || props === null) {
      return;
    }
    const record = props as Record<string, unknown>;
    if (type === "input" && record.type === "search" && typeof record.onChange === "function") {
      assetSearchRuntime.inputs.push({
        accessibleName: String(record["aria-label"] ?? ""),
        onChange: record.onChange as (event: unknown) => void,
      });
    }
    if (type === "button" && record["aria-label"] === "清空搜索" && typeof record.onClick === "function") {
      assetSearchRuntime.clearButtons.push({ onClick: record.onClick as (event: unknown) => void });
    }
  };
  const jsx: typeof actual.jsx = (...args) => {
    capture(args[0], args[1]);
    return actual.jsx(...args);
  };
  const jsxs: typeof actual.jsxs = (...args) => {
    capture(args[0], args[1]);
    return actual.jsxs(...args);
  };
  return { ...actual, jsx, jsxs };
});

vi.mock("react/jsx-dev-runtime", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react/jsx-dev-runtime")>();
  const jsxDEV: typeof actual.jsxDEV = (...args) => {
    const type = args[0];
    const props = args[1];
    if (typeof props === "object" && props !== null) {
      const record = props as Record<string, unknown>;
      if (type === "input" && record.type === "search" && typeof record.onChange === "function") {
        assetSearchRuntime.inputs.push({
          accessibleName: String(record["aria-label"] ?? ""),
          onChange: record.onChange as (event: unknown) => void,
        });
      }
      if (type === "button" && record["aria-label"] === "清空搜索" && typeof record.onClick === "function") {
        assetSearchRuntime.clearButtons.push({ onClick: record.onClick as (event: unknown) => void });
      }
    }
    return actual.jsxDEV(...args);
  };
  return { ...actual, jsxDEV };
});

function latestCapturedSearchInput(accessibleName: string) {
  const input = assetSearchRuntime.inputs.filter((entry) => entry.accessibleName === accessibleName).at(-1);
  if (input === undefined) {
    throw new Error(`No captured search input handler for ${accessibleName}.`);
  }
  return input.onChange;
}

function latestCapturedSearchClear() {
  const clear = assetSearchRuntime.clearButtons.at(-1);
  if (clear === undefined) {
    throw new Error("No captured asset search clear handler.");
  }
  return clear.onClick;
}

const series = demoSeries[0]!;
const apiBaseUrl = "http://127.0.0.1:4175/api";

function character(overrides: Partial<Character> = {}): Character {
  return {
    id: "shared-asset-id",
    series_id: series.id,
    name: "顾行舟",
    gender: "男",
    age: "31",
    role: "档案馆管理员",
    appearance: "细框眼镜，深色外套。",
    description: "保管旧城档案。",
    image_url: null,
    audio_url: "https://remote.example.invalid/voice.mp3",
    voice_ref: "private-voice-reference",
    aliases: ["行舟", "", "顾先生"],
    canonical_key: "internal-canonical-value",
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
    ...overrides,
  };
}

function scene(overrides: Partial<Scene> = {}): Scene {
  return {
    id: "shared-asset-id",
    series_id: series.id,
    title: "档案馆地下室",
    description: "一束斜光落在满是纸箱的房间里。",
    image_url: null,
    aliases: null,
    canonical_key: "internal-scene-canonical-value",
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
    ...overrides,
  };
}

function prop(overrides: Partial<Prop> = {}): Prop {
  return {
    id: "shared-asset-id",
    series_id: series.id,
    name: "旧铜钥匙",
    description: "齿口磨损，钥匙柄刻着一个数字。",
    image_url: null,
    aliases: ["储物柜钥匙"],
    canonical_key: "internal-prop-canonical-value",
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
    ...overrides,
  };
}

function chapter(chapterId: string, text: string, assetId = "shared-asset-id"): Chapter {
  return {
    id: chapterId,
    series_id: series.id,
    title: chapterId,
    content: [{ character: [assetId], text, original_text: null }],
    order: 1,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    lock: null,
  };
}

function makeServices(overrides: Partial<WorkspaceServices> = {}) {
  const services: WorkspaceServices = {
    mode: "api",
    apiBaseUrl,
    restore: async () => null,
    login: async () => {
      throw new Error("Not used by this component test.");
    },
    listSeries: async () => [],
    listMyTeams: async () => [],
    listChapters: vi.fn(async () => []),
    listStoryboardAssets: async () => [],
    listCharacters: vi.fn(async () => [character()]),
    listScenes: vi.fn(async () => [scene()]),
    listProps: vi.fn(async () => [prop()]),
    getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
    getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
    listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    logout: vi.fn(),
    ...overrides,
  };
  return services;
}

function renderLibrary(
  services: WorkspaceServices,
  onUnauthorized = vi.fn(),
  selectedSeries = series,
  userId = "demo-user",
) {
  return render(
    <AssetLibrary
      onBack={vi.fn()}
      onUnauthorized={onUnauthorized}
      series={selectedSeries}
      services={services}
      userId={userId}
    />,
  );
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

function LayoutCommitObserver({ onCommit }: { onCommit(): void }) {
  useLayoutEffect(() => {
    onCommit();
  });
  return null;
}

describe("read-only asset library", () => {
  afterEach(() => {
    cleanup();
    assetSearchRuntime.inputs = [];
    assetSearchRuntime.clearButtons = [];
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("starts with characters, renders readable fields as text, and hides media references", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const serviceSet = makeServices({
      listCharacters: vi.fn(async () => [
        character({ description: "<script>window.bad = true</script>" }),
      ]),
    });
    const { container } = renderLibrary(serviceSet);

    expect(await screen.findByRole("heading", { name: "顾行舟" })).toBeInTheDocument();
    expect(screen.getByText("行舟、顾先生")).toBeInTheDocument();
    expect(screen.getByText("档案馆管理员")).toBeInTheDocument();
    expect(screen.getByText("细框眼镜，深色外套。")).toBeInTheDocument();
    expect(screen.getByText("<script>window.bad = true</script>")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "顾行舟没有可显示的本地图片" })).toBeInTheDocument();
    expect(screen.queryByText(/internal-canonical|voice-reference|voice\.mp3/)).not.toBeInTheDocument();
    expect(container.querySelector("audio, audio source, script")).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(serviceSet.listChapters).not.toHaveBeenCalled();
  });

  it("opens a new snapshot for the selected card and ignores a late response from the previous asset", async () => {
    const user = userEvent.setup();
    const firstAsset = character({ id: "lin-asset", name: "林岚" });
    const secondAsset = character({ id: "gu-asset", name: "顾行舟" });
    const lateChapters = deferred<Chapter[]>();
    const listChapters = vi.fn()
      .mockReturnValueOnce(lateChapters.promise)
      .mockResolvedValueOnce([chapter("顾行舟所在章节", "顾行舟的关联镜头", "gu-asset")]);
    const onUnauthorized = vi.fn();
    const services = makeServices({
      listCharacters: vi.fn(async () => [firstAsset, secondAsset]),
      listChapters,
    });
    const capturedPanelProps: AssetFrameUsagePanelProps[] = [];
    const originalPanel = usagePanelModule.AssetFrameUsagePanel;
    const panelSpy = vi.spyOn(usagePanelModule, "AssetFrameUsagePanel").mockImplementation((props) => {
      capturedPanelProps.push(props);
      return originalPanel(props);
    });
    const { container } = renderLibrary(services, onUnauthorized);

    expect(await screen.findByRole("heading", { name: "林岚" })).toBeInTheDocument();
    expect(listChapters).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "查看关联镜头：林岚" }));
    expect(await screen.findByText("正在读取当前剧集章节…")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看关联镜头：林岚" }));
    expect(listChapters).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "查看关联镜头：顾行舟" }));
    expect(await screen.findByText("顾行舟的关联镜头")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "关联镜头" })).toHaveTextContent("顾行舟");
    expect(listChapters).toHaveBeenCalledTimes(2);
    const previousPanel = capturedPanelProps.find((props) => props.assetLabel === "林岚");
    expect(previousPanel).toBeDefined();
    act(() => previousPanel?.onClose(previousPanel.owner));
    expect(screen.getByText("顾行舟的关联镜头")).toBeInTheDocument();

    await act(async () => {
      lateChapters.resolve([chapter("林岚所在章节", "迟到的林岚镜头", "lin-asset")]);
      await lateChapters.promise;
    });
    expect(screen.getByText("顾行舟的关联镜头")).toBeInTheDocument();
    expect(screen.queryByText("迟到的林岚镜头")).not.toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(container.querySelector("audio, video, script")).toBeNull();
    expect(container.textContent).not.toMatch(/lin-asset|gu-asset|https?:/);
    panelSpy.mockRestore();
  });

  it("closes the usage panel on a category change and explicit directory reread", async () => {
    const user = userEvent.setup();
    const listChapters = vi.fn(async () => [chapter("关联章节", "当前角色镜头")]);
    const services = makeServices({
      listCharacters: vi.fn(async () => [character({ image_url: "http://127.0.0.1:4175/media/character.png" })]),
      listChapters,
    });
    renderLibrary(services);

    expect(await screen.findByRole("heading", { name: "顾行舟" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看关联镜头：顾行舟" }));
    expect(await screen.findByText("当前角色镜头")).toBeInTheDocument();
    fireEvent.error(screen.getByAltText("顾行舟图片"));
    expect(screen.getByText("当前角色镜头")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "场景" }));
    expect(screen.queryByRole("region", { name: "关联镜头" })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "档案馆地下室" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(await screen.findByRole("heading", { name: "顾行舟" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "关联镜头" })).not.toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "查看关联镜头：顾行舟" }));
    expect(await screen.findByText("当前角色镜头")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新读取" }));
    expect(screen.queryByRole("region", { name: "关联镜头" })).not.toBeInTheDocument();
    await waitFor(() => expect(services.listCharacters).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("region", { name: "关联镜头" })).not.toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(2);
  });

  it("hides the old directory on the first render after a services-scope change", async () => {
    const firstServices = makeServices({
      listCharacters: vi.fn(async () => [character({ id: "old-directory", name: "旧服务目录" })]),
    });
    const secondServices = makeServices({
      listCharacters: vi.fn(async () => [character({ id: "new-directory", name: "新服务目录" })]),
    });
    const view = renderLibrary(firstServices);
    expect(await screen.findByRole("heading", { name: "旧服务目录" })).toBeInTheDocument();

    view.rerender(
      <AssetLibrary
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
        series={series}
        services={secondServices}
        userId="demo-user"
      />,
    );
    expect(screen.queryByRole("heading", { name: "旧服务目录" })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "新服务目录" })).toBeInTheDocument();
    expect(secondServices.listCharacters).toHaveBeenCalledTimes(1);
    expect(secondServices.listChapters).not.toHaveBeenCalled();
  });

  it.each(["user-only", "services-only"] as const)(
    "hides a ready directory and open panel on the first layout commit for %s scope changes",
    async (changeKind) => {
      const user = userEvent.setup();
      const oldAsset = character({ id: "scope-asset", name: "作用域素材" });
      const otherAsset = character({ id: "other-scope-asset", name: "其他作用域素材" });
      const oldDirectory = [oldAsset];
      const otherDirectory = [otherAsset];
      const listChapters = vi.fn(async () => [chapter("scope-chapter", "原作用域镜头", "scope-asset")]);
      const firstListCharacters = changeKind === "user-only"
        ? vi.fn()
            .mockResolvedValueOnce(oldDirectory)
            .mockResolvedValueOnce(otherDirectory)
            .mockResolvedValueOnce(oldDirectory)
        : vi.fn()
            .mockResolvedValueOnce(oldDirectory)
            .mockResolvedValueOnce(oldDirectory);
      const firstServices = makeServices({ listCharacters: firstListCharacters, listChapters });
      const secondServices = makeServices({
        listCharacters: vi.fn(async () => otherDirectory),
      });
      const observations: Array<{ oldCard: boolean; oldEntry: boolean; panel: boolean }> = [];
      let observeNextCommit = false;

      function ScopeHarness({ currentServices, currentUserId }: {
        currentServices: WorkspaceServices;
        currentUserId: string;
      }) {
        return (
          <>
            <AssetLibrary
              onBack={vi.fn()}
              onUnauthorized={vi.fn()}
              series={series}
              services={currentServices}
              userId={currentUserId}
            />
            <LayoutCommitObserver onCommit={() => {
              if (!observeNextCommit) {
                return;
              }
              observeNextCommit = false;
              observations.push({
                oldCard: screen.queryByRole("heading", { name: "作用域素材" }) !== null,
                oldEntry: screen.queryByRole("button", { name: "查看关联镜头：作用域素材" }) !== null,
                panel: screen.queryByRole("region", { name: "关联镜头" }) !== null,
              });
            }} />
          </>
        );
      }

      const initialUserId = "scope-user-a";
      const view = render(<ScopeHarness currentServices={firstServices} currentUserId={initialUserId} />);
      expect(await screen.findByRole("heading", { name: "作用域素材" })).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "查看关联镜头：作用域素材" }));
      expect(await screen.findByText("原作用域镜头")).toBeInTheDocument();
      expect(listChapters).toHaveBeenCalledTimes(1);

      observeNextCommit = true;
      view.rerender(
        <ScopeHarness
          currentServices={changeKind === "user-only" ? firstServices : secondServices}
          currentUserId={changeKind === "user-only" ? "scope-user-b" : initialUserId}
        />,
      );
      expect(observations.at(-1)).toEqual({ oldCard: false, oldEntry: false, panel: false });
      expect(await screen.findByRole("heading", { name: "其他作用域素材" })).toBeInTheDocument();

      observeNextCommit = true;
      view.rerender(<ScopeHarness currentServices={firstServices} currentUserId={initialUserId} />);
      expect(observations.at(-1)).toEqual({ oldCard: false, oldEntry: false, panel: false });
      expect(await screen.findByRole("heading", { name: "作用域素材" })).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "关联镜头" })).not.toBeInTheDocument();
      expect(listChapters).toHaveBeenCalledTimes(1);
      expect(firstServices.listCharacters).toHaveBeenCalledTimes(changeKind === "user-only" ? 3 : 2);

      await user.click(screen.getByRole("button", { name: "查看关联镜头：作用域素材" }));
      expect(await screen.findByText("原作用域镜头")).toBeInTheDocument();
      expect(listChapters).toHaveBeenCalledTimes(2);
    },
  );

  it("does not revive an open usage panel after a services A to B to A cycle", async () => {
    const user = userEvent.setup();
    const sameAsset = character({ id: "scope-asset", name: "作用域素材" });
    const firstDirectory = [sameAsset];
    const firstChapters = vi.fn(async () => [chapter("first-snapshot", "第一次显式快照", "scope-asset")]);
    const firstServices = makeServices({
      listCharacters: vi.fn(async () => firstDirectory),
      listChapters: firstChapters,
    });
    const secondServices = makeServices({
      listCharacters: vi.fn(async () => [sameAsset]),
    });
    const view = renderLibrary(firstServices);

    expect(await screen.findByRole("heading", { name: "作用域素材" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看关联镜头：作用域素材" }));
    expect(await screen.findByText("第一次显式快照")).toBeInTheDocument();
    expect(firstChapters).toHaveBeenCalledTimes(1);

    view.rerender(
      <AssetLibrary
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
        series={series}
        services={secondServices}
        userId="demo-user"
      />,
    );
    expect(screen.queryByRole("region", { name: "关联镜头" })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "作用域素材" })).toBeInTheDocument();
    view.rerender(
      <AssetLibrary
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
        series={series}
        services={firstServices}
        userId="demo-user"
      />,
    );
    expect(screen.queryByRole("region", { name: "关联镜头" })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "作用域素材" })).toBeInTheDocument();
    expect(firstServices.listCharacters).toHaveBeenCalledTimes(2);
    expect(firstChapters).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "查看关联镜头：作用域素材" }));
    expect(await screen.findByText("第一次显式快照")).toBeInTheDocument();
    expect(firstChapters).toHaveBeenCalledTimes(2);
  });

  it("ignores a late directory 401 after the user changes and the new directory loads", async () => {
    const oldDirectory = deferred<Character[]>();
    const listCharacters = vi.fn()
      .mockReturnValueOnce(oldDirectory.promise)
      .mockResolvedValueOnce([character({ id: "new-user-directory", name: "新用户角色" })]);
    const onUnauthorized = vi.fn();
    const services = makeServices({ listCharacters });
    const view = renderLibrary(services, onUnauthorized, series, "user-a");
    expect(await screen.findByText("正在读取角色…")).toBeInTheDocument();

    view.rerender(
      <AssetLibrary
        onBack={vi.fn()}
        onUnauthorized={onUnauthorized}
        series={series}
        services={services}
        userId="user-b"
      />,
    );
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "新用户角色" })).toBeInTheDocument();

    await act(async () => {
      oldDirectory.reject(new ApiError("http", "expired", 401));
      await oldDirectory.promise.catch(() => undefined);
    });
    expect(screen.getByRole("heading", { name: "新用户角色" })).toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(listCharacters).toHaveBeenCalledTimes(2);
  });

  it("shows no usage entry for blank, duplicated, or foreign asset identities", async () => {
    const services = makeServices({
      listCharacters: vi.fn(async () => [
        character({ id: "duplicate-id", name: "重复甲" }),
        character({ id: "duplicate-id", name: "重复乙" }),
        character({ id: "   ", name: "空身份" }),
        character({ id: "foreign-id", series_id: "another-series", name: "其他剧集" }),
      ]),
    });
    renderLibrary(services);

    expect(await screen.findByRole("heading", { name: "重复甲" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /查看关联镜头/ })).not.toBeInTheDocument();
    expect(screen.getAllByText("素材身份暂无法核对，不能查看关联镜头。")).toHaveLength(4);
    expect(services.listChapters).not.toHaveBeenCalled();
  });

  it("reads only the requested category once in StrictMode, focuses its exact card, and consumes the intent", async () => {
    const user = userEvent.setup();
    const sceneRecord = scene({ id: "target-scene", title: "目标场景" });
    const listCharacters = vi.fn(async () => [character()]);
    const listScenes = vi.fn(async () => [sceneRecord]);
    const listProps = vi.fn(async () => [prop()]);
    const services = makeServices({ listCharacters, listScenes, listProps });
    const intent: AssetNavigationIntent = {
      userId: "demo-user",
      services,
      seriesId: series.id,
      category: "scenes",
      assetId: sceneRecord.id,
      navigationEpoch: 91,
    };
    const onConsumed = vi.fn();
    const onAbandoned = vi.fn();
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });

    function NavigatedLibrary() {
      const [currentIntent, setCurrentIntent] = useState<AssetNavigationIntent | null>(intent);
      return (
        <AssetLibrary
          navigationIntent={currentIntent}
          onBack={vi.fn()}
          onNavigationAbandoned={onAbandoned}
          onNavigationConsumed={(epoch) => {
            onConsumed(epoch);
            setCurrentIntent(null);
          }}
          onUnauthorized={vi.fn()}
          series={series}
          services={services}
          userId="demo-user"
        />
      );
    }

    render(<StrictMode><NavigatedLibrary /></StrictMode>);
    const card = await screen.findByRole("article", { name: "目标场景素材卡片" });
    await waitFor(() => expect(card).toHaveFocus());
    expect(screen.getByRole("status")).toHaveTextContent("已定位到对应素材");
    fireEvent.blur(card, { relatedTarget: card.querySelector("h2") });
    expect(screen.queryByText("已定位到对应素材")).not.toBeInTheDocument();
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "auto", block: "center" });
    expect(onConsumed).toHaveBeenCalledExactlyOnceWith(intent.navigationEpoch);
    expect(onAbandoned).not.toHaveBeenCalled();
    expect(listCharacters).not.toHaveBeenCalled();
    expect(listScenes).toHaveBeenCalledTimes(1);
    expect(listProps).not.toHaveBeenCalled();
    expect(document.querySelector("audio, video")).toBeNull();

    await user.click(screen.getByRole("button", { name: "重新读取" }));
    await waitFor(() => expect(listScenes).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("article", { name: "目标场景素材卡片" })).not.toHaveFocus();

    if (originalScrollIntoView === undefined) {
      Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
    } else {
      Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
        configurable: true,
        value: originalScrollIntoView,
      });
    }
  });

  it.each(["ready", "error"] as const)(
    "adopts a new %s navigation intent before the first commit and reads only its category",
    async (oldState) => {
      const oldCharacter = character({ id: "old-character", name: "旧类别素材" });
      const targetScene = scene({ id: "target-scene", title: "目标场景" });
      const listCharacters = oldState === "ready"
        ? vi.fn(async () => [oldCharacter])
        : vi.fn(async () => { throw new ApiError("http", "failed", 500); });
      const listScenes = vi.fn(async () => [targetScene]);
      const listProps = vi.fn(async () => [prop()]);
      const services = makeServices({ listCharacters, listScenes, listProps });
      const oldIntent: AssetNavigationIntent = {
        userId: "demo-user",
        services,
        seriesId: series.id,
        category: "characters",
        assetId: oldCharacter.id,
        navigationEpoch: 93,
      };
      const nextIntent: AssetNavigationIntent = {
        userId: "demo-user",
        services,
        seriesId: series.id,
        category: "scenes",
        assetId: targetScene.id,
        navigationEpoch: 94,
      };
      const commits: Array<{ oldCard: boolean; oldError: boolean; scenesSelected: boolean }> = [];
      const observeCommit = () => {
        commits.push({
          oldCard: screen.queryByRole("article", { name: "旧类别素材素材卡片" }) !== null,
          oldError: screen.queryByRole("alert") !== null,
          scenesSelected: screen.queryByRole("button", { name: /^场景/ })?.getAttribute("aria-pressed") === "true",
        });
      };
      const renderIntent = (intent: AssetNavigationIntent) => (
        <StrictMode>
          <>
            <AssetLibrary
              navigationIntent={intent}
              onBack={vi.fn()}
              onNavigationConsumed={vi.fn()}
              onUnauthorized={vi.fn()}
              series={series}
              services={services}
              userId="demo-user"
            />
            <LayoutCommitObserver onCommit={observeCommit} />
          </>
        </StrictMode>
      );

      const view = render(renderIntent(oldIntent));
      if (oldState === "ready") {
        expect(await screen.findByRole("article", { name: "旧类别素材素材卡片" })).toBeInTheDocument();
      } else {
        expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法读取素材");
      }
      expect(listCharacters).toHaveBeenCalledTimes(1);
      const commitIndex = commits.length;
      view.rerender(renderIntent(nextIntent));

      expect(commits[commitIndex]).toEqual({ oldCard: false, oldError: false, scenesSelected: true });
      expect(screen.queryByRole("article", { name: "旧类别素材素材卡片" })).not.toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(await screen.findByRole("article", { name: "目标场景素材卡片" })).toHaveFocus();
      expect(listCharacters).toHaveBeenCalledTimes(1);
      expect(listScenes).toHaveBeenCalledTimes(1);
      expect(listProps).not.toHaveBeenCalled();
    },
  );

  it.each(["success", "401"] as const)(
    "does not let a superseded target-category %s overwrite or log out after a fresh retry succeeds",
    async (lateResult) => {
      const user = userEvent.setup();
      const staleRequest = deferred<Scene[]>();
      const targetScene = scene({ id: "reread-target", title: "重核后的场景" });
      const listScenes = vi.fn()
        .mockReturnValueOnce(staleRequest.promise)
        .mockResolvedValueOnce([targetScene]);
      const services = makeServices({ listScenes });
      const intent: AssetNavigationIntent = {
        userId: "demo-user",
        services,
        seriesId: series.id,
        category: "scenes",
        assetId: targetScene.id,
        navigationEpoch: 98,
      };
      const onUnauthorized = vi.fn();
      const onConsumed = vi.fn();
      render(
        <AssetLibrary
          navigationIntent={intent}
          onBack={vi.fn()}
          onNavigationConsumed={onConsumed}
          onUnauthorized={onUnauthorized}
          series={series}
          services={services}
          userId="demo-user"
        />,
      );

      expect(await screen.findByText("正在读取场景…")).toBeInTheDocument();
      await waitFor(() => expect(listScenes).toHaveBeenCalledTimes(1));
      await user.click(screen.getByRole("button", { name: "重新读取" }));
      await waitFor(() => expect(listScenes).toHaveBeenCalledTimes(2));
      expect(await screen.findByRole("article", { name: "重核后的场景素材卡片" })).toHaveFocus();
      expect(onConsumed).toHaveBeenCalledExactlyOnceWith(intent.navigationEpoch);

      await act(async () => {
        if (lateResult === "success") {
          staleRequest.resolve([scene({ id: targetScene.id, title: "迟到场景" })]);
          await staleRequest.promise;
        } else {
          staleRequest.reject(new ApiError("http", "expired", 401));
          await staleRequest.promise.catch(() => undefined);
        }
        await Promise.resolve();
      });

      expect(screen.getByRole("article", { name: "重核后的场景素材卡片" })).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "迟到场景" })).not.toBeInTheDocument();
      expect(onUnauthorized).not.toHaveBeenCalled();
      expect(listScenes).toHaveBeenCalledTimes(2);
    },
  );

  it.each(["focus", "hidden", "detached"] as const)(
    "retains a current intent for explicit retry when the target cannot be focused because it is %s",
    async (failureMode) => {
      const user = userEvent.setup();
      const targetScene = scene({ id: "focus-target", title: "焦点目标场景" });
      const services = makeServices({ listScenes: vi.fn(async () => [targetScene]) });
      const intent: AssetNavigationIntent = {
        userId: "demo-user",
        services,
        seriesId: series.id,
        category: "scenes",
        assetId: targetScene.id,
        navigationEpoch: 95,
      };
      const onConsumed = vi.fn();
      const onAbandoned = vi.fn();
      const originalFocus = HTMLElement.prototype.focus;
      const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
      const scrollIntoView = vi.fn();
      Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
        configurable: true,
        value: scrollIntoView,
      });
      let failFirstTargetFocus = true;
      const detachedTarget: Array<{
        element: HTMLElement;
        parent: Node;
        nextSibling: Node | null;
      } | null> = [null];
      const focusSpy = vi.spyOn(HTMLElement.prototype, "focus").mockImplementation(function (
        this: HTMLElement,
        options?: FocusOptions,
      ) {
        if (this.getAttribute("aria-label") === "焦点目标场景素材卡片" && failFirstTargetFocus) {
          failFirstTargetFocus = false;
          if (failureMode === "hidden") {
            this.setAttribute("hidden", "");
          } else if (failureMode === "detached") {
            const parent = this.parentNode;
            if (parent) {
              detachedTarget[0] = {
                element: this,
                parent,
                nextSibling: this.nextSibling,
              };
              this.remove();
            }
          }
          return;
        }
        originalFocus.call(this, options);
      });

      render(
        <AssetLibrary
          navigationIntent={intent}
          onBack={vi.fn()}
          onNavigationAbandoned={onAbandoned}
          onNavigationConsumed={onConsumed}
          onUnauthorized={vi.fn()}
          series={series}
          services={services}
          userId="demo-user"
        />,
      );

      expect(await screen.findByText("浏览器未能将焦点移到素材卡片。可重新核对。"))
        .toBeInTheDocument();
      expect(onConsumed).not.toHaveBeenCalled();
      expect(onAbandoned).not.toHaveBeenCalled();
      expect(scrollIntoView).not.toHaveBeenCalled();
      expect(services.listScenes).toHaveBeenCalledTimes(1);

      const detached = detachedTarget[0];
      if (detached) {
        detached.parent.insertBefore(
          detached.element,
          detached.nextSibling?.parentNode === detached.parent ? detached.nextSibling : null,
        );
      }
      await user.click(screen.getByRole("button", { name: "重新核对" }));
      const card = await screen.findByRole("article", { name: "焦点目标场景素材卡片" });
      await waitFor(() => expect(card).toHaveFocus());
      expect(services.listScenes).toHaveBeenCalledTimes(2);
      expect(onConsumed).toHaveBeenCalledExactlyOnceWith(intent.navigationEpoch);
      expect(onAbandoned).not.toHaveBeenCalled();
      expect(scrollIntoView).toHaveBeenCalledTimes(1);

      focusSpy.mockRestore();
      if (originalScrollIntoView === undefined) {
        Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
      } else {
        Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
          configurable: true,
          value: originalScrollIntoView,
        });
      }
    },
  );

  it("keeps a same-category navigation request when its selected tab is clicked while pending", async () => {
    const user = userEvent.setup();
    const pending = deferred<Scene[]>();
    const targetScene = scene({ id: "pending-scene", title: "等待中的场景" });
    const listScenes = vi.fn(() => pending.promise);
    const services = makeServices({ listScenes });
    const intent: AssetNavigationIntent = {
      userId: "demo-user",
      services,
      seriesId: series.id,
      category: "scenes",
      assetId: targetScene.id,
      navigationEpoch: 96,
    };
    const onConsumed = vi.fn();
    const onAbandoned = vi.fn();
    render(
      <AssetLibrary
        navigationIntent={intent}
        onBack={vi.fn()}
        onNavigationAbandoned={onAbandoned}
        onNavigationConsumed={onConsumed}
        onUnauthorized={vi.fn()}
        series={series}
        services={services}
        userId="demo-user"
      />,
    );

    expect(await screen.findByText("正在读取场景…")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^场景/ }));
    expect(listScenes).toHaveBeenCalledTimes(1);
    expect(onAbandoned).not.toHaveBeenCalled();

    await act(async () => {
      pending.resolve([targetScene]);
      await pending.promise;
    });
    expect(await screen.findByRole("article", { name: "等待中的场景素材卡片" })).toHaveFocus();
    expect(onConsumed).toHaveBeenCalledExactlyOnceWith(intent.navigationEpoch);
    expect(listScenes).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByRole("article", { name: "等待中的场景素材卡片" })).toBeInTheDocument();
    expect(onConsumed).toHaveBeenCalledExactlyOnceWith(intent.navigationEpoch);
    expect(listScenes).toHaveBeenCalledTimes(2);
  });

  it("abandons a pending target on a different category and ignores its late success", async () => {
    const user = userEvent.setup();
    const pending = deferred<Scene[]>();
    const listScenes = vi.fn(() => pending.promise);
    const listCharacters = vi.fn(async () => [character()]);
    const services = makeServices({ listScenes, listCharacters });
    const intent: AssetNavigationIntent = {
      userId: "demo-user",
      services,
      seriesId: series.id,
      category: "scenes",
      assetId: "pending-scene",
      navigationEpoch: 97,
    };
    const onConsumed = vi.fn();
    const onAbandoned = vi.fn();

    render(
      <AssetLibrary
        navigationIntent={intent}
        onBack={vi.fn()}
        onNavigationAbandoned={onAbandoned}
        onNavigationConsumed={onConsumed}
        onUnauthorized={vi.fn()}
        series={series}
        services={services}
        userId="demo-user"
      />,
    );
    expect(await screen.findByText("正在读取场景…")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(await screen.findByRole("heading", { name: "顾行舟" })).toBeInTheDocument();
    expect(onAbandoned).toHaveBeenCalledExactlyOnceWith(intent.navigationEpoch);
    expect(listScenes).toHaveBeenCalledTimes(1);
    expect(listCharacters).toHaveBeenCalledTimes(1);

    await act(async () => {
      pending.resolve([scene({ id: "pending-scene", title: "迟到目标" })]);
      await pending.promise;
    });
    expect(screen.queryByRole("heading", { name: "迟到目标" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "顾行舟" })).toBeInTheDocument();
    expect(onConsumed).not.toHaveBeenCalled();
    expect(onAbandoned).toHaveBeenCalledTimes(1);
  });

  it("keeps an ambiguous target pending for a full explicit category recheck", async () => {
    const duplicate = character({ id: "target-character", name: "目标角色" });
    const listCharacters = vi.fn()
      .mockResolvedValueOnce([
        duplicate,
        character({ id: duplicate.id, series_id: "another-series", name: "其他剧集记录" }),
      ])
      .mockResolvedValueOnce([duplicate]);
    const services = makeServices({ listCharacters });
    const intent: AssetNavigationIntent = {
      userId: "demo-user",
      services,
      seriesId: series.id,
      category: "characters",
      assetId: duplicate.id,
      navigationEpoch: 92,
    };
    const onConsumed = vi.fn();
    const onAbandoned = vi.fn();
    const user = userEvent.setup();
    render(
      <AssetLibrary
        navigationIntent={intent}
        onBack={vi.fn()}
        onNavigationAbandoned={onAbandoned}
        onNavigationConsumed={onConsumed}
        onUnauthorized={vi.fn()}
        series={series}
        services={services}
        userId="demo-user"
      />,
    );

    expect(await screen.findByText("当前分类中有多项素材身份相同，暂时无法定位。可重新核对。"))
      .toBeInTheDocument();
    expect(onConsumed).not.toHaveBeenCalled();
    expect(onAbandoned).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "重新核对" })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "目标角色素材卡片" })).not.toHaveFocus();

    await user.click(screen.getByRole("button", { name: "重新核对" }));
    const card = await screen.findByRole("article", { name: "目标角色素材卡片" });
    await waitFor(() => expect(card).toHaveFocus());
    expect(listCharacters).toHaveBeenCalledTimes(2);
    expect(onConsumed).toHaveBeenCalledExactlyOnceWith(intent.navigationEpoch);
    expect(onAbandoned).not.toHaveBeenCalled();
  });

  it("preserves API order and does not refetch when the selected type is clicked again", async () => {
    const user = userEvent.setup();
    const listCharacters = vi.fn(async () => [
      character({ id: "character-z", name: "先返回角色" }),
      character({ id: "character-a", name: "后返回角色" }),
    ]);
    renderLibrary(makeServices({ listCharacters }));

    expect(await screen.findByRole("heading", { name: "先返回角色" })).toBeInTheDocument();
    expect(screen.getAllByRole("article").map((card) => card.querySelector("h2")?.textContent))
      .toEqual(["先返回角色", "后返回角色"]);
    await user.click(screen.getByRole("button", { name: /^角色/ }));
    expect(listCharacters).toHaveBeenCalledTimes(1);
  });

  it("does not cache a late category response and keeps image failures scoped to the type", async () => {
    const user = userEvent.setup();
    const firstCharacter = character({ id: "first-character", name: "先返回角色" });
    const sameIdCharacter = character({
      image_url: "http://127.0.0.1:4175/media/character-same-id.png",
      description: "<script>window.bad = true</script>",
    });
    const lateCharacters = deferred<Character[]>();
    const onUnauthorized = vi.fn();
    const services = makeServices({
      listCharacters: vi.fn()
        .mockReturnValueOnce(lateCharacters.promise)
        .mockResolvedValueOnce([character({
          name: "当前有效角色",
          image_url: "http://127.0.0.1:4175/media/current-character.png",
        })]),
      listScenes: vi.fn(async () => [
        scene({ image_url: "http://127.0.0.1:4175/media/scene-same-id.png" }),
      ]),
      listProps: vi.fn(async () => [prop()]),
    });
    renderLibrary(services, onUnauthorized);

    expect(await screen.findByText("正在读取角色…")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "场景" }));
    expect(await screen.findByRole("heading", { name: "档案馆地下室" })).toBeInTheDocument();
    expect(screen.getByAltText("档案馆地下室图片")).toHaveAttribute(
      "src",
      "http://127.0.0.1:4175/media/scene-same-id.png",
    );
    await user.click(screen.getByRole("button", { name: "道具" }));
    expect(await screen.findByRole("heading", { name: "旧铜钥匙" })).toBeInTheDocument();

    await act(async () => {
      lateCharacters.resolve([firstCharacter, sameIdCharacter]);
      await lateCharacters.promise;
    });
    await user.click(screen.getByRole("button", { name: /^角色/ }));
    expect(await screen.findByRole("heading", { name: "当前有效角色" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "先返回角色" })).not.toBeInTheDocument();
    const characterImage = screen.getByAltText("当前有效角色图片");
    fireEvent.error(characterImage);
    expect(await screen.findByRole("img", { name: "当前有效角色没有可显示的本地图片" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "场景" }));
    expect(await screen.findByAltText("档案馆地下室图片")).toHaveAttribute(
      "src",
      "http://127.0.0.1:4175/media/scene-same-id.png",
    );
    await user.click(screen.getByRole("button", { name: /^角色/ }));
    expect(await screen.findByRole("heading", { name: "当前有效角色" })).toBeInTheDocument();
    expect(services.listCharacters).toHaveBeenCalledTimes(2);
    expect(services.listScenes).toHaveBeenCalledTimes(1);
    expect(services.listProps).toHaveBeenCalledTimes(1);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("filters visible names and aliases while preserving the complete card nodes and image state", async () => {
    const user = userEvent.setup();
    const matchingAlias = character({
      id: "alias-match",
      name: "阿岚",
      aliases: ["林编辑", ""],
      image_url: "http://127.0.0.1:4175/media/alias-match.png",
    });
    const hiddenDescription = character({
      id: "description-only",
      name: "林岚",
      aliases: ["普通别名"],
      description: "林编辑仅出现在描述里。",
      image_url: "http://127.0.0.1:4175/media/hidden-description.png",
    });
    const unnamedAlias = character({
      id: "unnamed-alias",
      name: "",
      aliases: ["真别名"],
    });
    const listCharacters = vi.fn(async () => [matchingAlias, hiddenDescription, unnamedAlias]);
    const services = makeServices({ listCharacters });
    const { container } = renderLibrary(services);

    expect(await screen.findByRole("searchbox", { name: "搜索角色素材" })).toBeInTheDocument();
    const originalCards = Array.from(container.querySelectorAll<HTMLElement>(".asset-grid > article"));
    expect(originalCards).toHaveLength(3);
    const [aliasCard, descriptionCard, unnamedCard] = originalCards;
    if (aliasCard === undefined || descriptionCard === undefined || unnamedCard === undefined) {
      throw new Error("Expected all original asset cards to be present.");
    }
    fireEvent.error(screen.getByAltText("阿岚图片"));
    fireEvent.error(screen.getByAltText("林岚图片"));
    expect(screen.getByRole("img", { name: "阿岚没有可显示的本地图片" })).toBeInTheDocument();

    const search = screen.getByRole("searchbox", { name: "搜索角色素材" });
    await user.type(search, " 林编 ");
    expect(await screen.findByText("显示 1 / 3 项素材")).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "阿岚素材卡片" })).toBe(aliasCard);
    expect(descriptionCard).toHaveAttribute("hidden");
    expect(descriptionCard).toHaveAttribute("inert");
    expect(unnamedCard).toHaveAttribute("hidden");
    expect(Array.from(container.querySelectorAll<HTMLElement>(".asset-grid > article")))
      .toEqual(originalCards);
    expect(container.querySelectorAll(".asset-grid img, .asset-grid audio, .asset-grid video"))
      .toHaveLength(0);
    expect(screen.getByRole("img", { name: "阿岚没有可显示的本地图片" })).toBeInTheDocument();

    await user.clear(search);
    expect(await screen.findByText("显示 3 / 3 项素材")).toBeInTheDocument();
    expect(Array.from(container.querySelectorAll<HTMLElement>(".asset-grid > article")))
      .toEqual(originalCards);
    expect(screen.getByRole("img", { name: "阿岚没有可显示的本地图片" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "林岚没有可显示的本地图片" })).toBeInTheDocument();

    await user.type(search, "未命名角色");
    expect(await screen.findByText("显示 0 / 3 项素材")).toBeInTheDocument();
    expect(screen.getByText("当前分类中没有匹配的素材。")).toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "清空搜索" }));
    expect(await screen.findByText("显示 3 / 3 项素材")).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox", { name: "搜索角色素材" }), "真别名");
    expect(await screen.findByText("显示 1 / 3 项素材")).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "未命名角色素材卡片" })).toBe(unnamedCard);
    expect(screen.getByRole("button", { name: "清空搜索" })).toBeEnabled();
    expect(listCharacters).toHaveBeenCalledTimes(1);
    expect(services.listChapters).not.toHaveBeenCalled();
  });

  it("keeps duplicate identity unauthorized when search leaves only one matching card", async () => {
    const duplicate = character({ id: "duplicate-search-id", name: "可见角色" });
    const services = makeServices({
      listCharacters: vi.fn(async () => [
        duplicate,
        character({ id: duplicate.id, name: "隐藏的重复角色" }),
      ]),
    });
    renderLibrary(services);

    const search = await screen.findByRole("searchbox", { name: "搜索角色素材" });
    await userEvent.setup().type(search, "可见");
    expect(await screen.findByText("显示 1 / 2 项素材")).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "可见角色素材卡片" }))
      .toHaveTextContent("素材身份暂无法核对，不能查看关联镜头。");
    expect(screen.queryByRole("button", { name: "查看关联镜头：可见角色" })).not.toBeInTheDocument();
    expect(services.listCharacters).toHaveBeenCalledTimes(1);
    expect(services.listChapters).not.toHaveBeenCalled();
  });

  it("rejects real old search handlers after a reread and a new navigation read", async () => {
    const user = userEvent.setup();
    const directory = [
      character({ id: "gu-character", name: "顾行舟", aliases: ["行舟"] }),
      character({ id: "lin-character", name: "林岚", aliases: ["阿岚"] }),
    ];
    const listCharacters = vi.fn(async () => directory);
    const listChapters = vi.fn(async () => [chapter("关联章节", "关联镜头", "gu-character")]);
    const services = makeServices({ listCharacters, listChapters });
    const onConsumed = vi.fn();
    const onAbandoned = vi.fn();
    const renderWithIntent = (navigationIntent: AssetNavigationIntent | null) => (
      <AssetLibrary
        navigationIntent={navigationIntent}
        onBack={vi.fn()}
        onNavigationAbandoned={onAbandoned}
        onNavigationConsumed={onConsumed}
        onUnauthorized={vi.fn()}
        series={series}
        services={services}
        userId="search-user"
      />
    );
    const view = render(renderWithIntent(null));

    expect(await screen.findByRole("heading", { name: "顾行舟" })).toBeInTheDocument();
    const r1Input = latestCapturedSearchInput("搜索角色素材");
    const r1Clear = latestCapturedSearchClear();
    await user.type(screen.getByRole("searchbox", { name: "搜索角色素材" }), "顾");
    expect(await screen.findByText("显示 1 / 2 项素材")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重新读取" }));
    await waitFor(() => expect(listCharacters).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("显示 2 / 2 项素材")).toBeInTheDocument();
    await user.type(screen.getByRole("searchbox", { name: "搜索角色素材" }), "林");
    expect(await screen.findByText("显示 1 / 2 项素材")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^角色/ }));
    expect(screen.getByRole("searchbox", { name: "搜索角色素材" })).toHaveValue("林");
    expect(listCharacters).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole("button", { name: "查看关联镜头：林岚" }));
    expect(await screen.findByText("关联镜头")).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(1);
    const r2Input = latestCapturedSearchInput("搜索角色素材");
    const r2Clear = latestCapturedSearchClear();

    act(() => {
      r1Input({ currentTarget: { value: "顾" } });
      r1Clear({});
    });
    expect(screen.getByRole("searchbox", { name: "搜索角色素材" })).toHaveValue("林");
    expect(screen.getByRole("article", { name: "林岚素材卡片" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "关联镜头" })).toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(2);
    expect(listChapters).toHaveBeenCalledTimes(1);

    act(() => r2Input({ currentTarget: { value: "岚" } }));
    expect(await screen.findByText("显示 1 / 2 项素材")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "关联镜头" })).not.toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(2);
    expect(listChapters).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "查看关联镜头：林岚" }));
    expect(await screen.findByRole("region", { name: "关联镜头" })).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(2);

    const nextIntent: AssetNavigationIntent = {
      userId: "search-user",
      services,
      seriesId: series.id,
      category: "characters",
      assetId: "gu-character",
      navigationEpoch: 301,
    };
    view.rerender(renderWithIntent(nextIntent));
    const guCard = await screen.findByRole("article", { name: "顾行舟素材卡片" });
    await waitFor(() => expect(guCard).toHaveFocus());
    expect(screen.getByRole("searchbox", { name: "搜索角色素材" })).toHaveValue("");
    expect(await screen.findByText("显示 2 / 2 项素材")).toBeInTheDocument();
    expect(onConsumed).toHaveBeenCalledExactlyOnceWith(nextIntent.navigationEpoch);
    expect(listCharacters).toHaveBeenCalledTimes(3);
    expect(screen.getByText("已定位到对应素材。")).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox", { name: "搜索角色素材" }), "顾");
    expect(await screen.findByText("显示 1 / 2 项素材")).toBeInTheDocument();
    expect(screen.queryByText("已定位到对应素材。")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看关联镜头：顾行舟" }));
    expect(await screen.findByRole("region", { name: "关联镜头" })).toBeInTheDocument();
    const currentInput = screen.getByRole("searchbox", { name: "搜索角色素材" });
    const currentQuery = (currentInput as HTMLInputElement).value;
    const chaptersReadBeforeReplay = listChapters.mock.calls.length;

    act(() => {
      r1Input({ currentTarget: { value: "林" } });
      r1Clear({});
      r2Clear({});
    });
    expect(screen.getByRole("searchbox", { name: "搜索角色素材" })).toHaveValue("顾");
    expect(screen.getByRole("article", { name: "顾行舟素材卡片" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "关联镜头" })).toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(3);
    expect(listChapters).toHaveBeenCalledTimes(chaptersReadBeforeReplay);
    expect(screen.getByRole("button", { name: "清空搜索" })).toBeEnabled();
    expect(currentQuery).toBe("顾");
    expect(onAbandoned).not.toHaveBeenCalled();
  });

  it.each(["user-only", "services-only", "series-only"] as const)(
    "hides a previous query on the first layout commit and permanently rejects it after %s ABA",
    async (changeKind) => {
      const firstAsset = character({ id: "first-scope-asset", name: "阿岚", aliases: ["林编"] });
      const secondAsset = character({ id: "second-scope-asset", name: "顾行舟" });
      const firstDirectory = [firstAsset];
      const secondDirectory = [secondAsset];
      const secondSeries = { ...series, id: "search-series-b" };
      const listCharactersA = changeKind === "user-only" || changeKind === "series-only"
        ? vi.fn()
            .mockResolvedValueOnce(firstDirectory)
            .mockResolvedValueOnce(secondDirectory)
            .mockResolvedValueOnce(firstDirectory)
        : vi.fn()
            .mockResolvedValueOnce(firstDirectory)
            .mockResolvedValueOnce(firstDirectory);
      const listCharactersB = vi.fn(async () => secondDirectory);
      const firstServices = makeServices({ listCharacters: listCharactersA });
      const secondServices = changeKind === "services-only"
        ? makeServices({ listCharacters: listCharactersB })
        : firstServices;
      const observations: Array<{ query: string | null; oldCard: boolean }> = [];
      let inspectNextCommit = false;
      function SearchScopeHarness({ services: currentServices, userId, selectedSeries }: {
        services: WorkspaceServices;
        userId: string;
        selectedSeries: typeof series;
      }) {
        return (
          <>
            <AssetLibrary
              onBack={vi.fn()}
              onUnauthorized={vi.fn()}
              series={selectedSeries}
              services={currentServices}
              userId={userId}
            />
            <LayoutCommitObserver onCommit={() => {
              if (!inspectNextCommit) {
                return;
              }
              inspectNextCommit = false;
              observations.push({
                query: screen.queryByRole("searchbox", { name: "搜索角色素材" })?.getAttribute("value") ?? null,
                oldCard: screen.queryByRole("article", { name: "阿岚素材卡片" }) !== null,
              });
            }} />
          </>
        );
      }

      const view = render(
        <SearchScopeHarness services={firstServices} userId="search-user-a" selectedSeries={series} />,
      );
      expect(await screen.findByRole("heading", { name: "阿岚" })).toBeInTheDocument();
      await userEvent.setup().type(screen.getByRole("searchbox", { name: "搜索角色素材" }), "林编");
      expect(await screen.findByText("显示 1 / 1 项素材")).toBeInTheDocument();
      const oldHandler = latestCapturedSearchInput("搜索角色素材");

      inspectNextCommit = true;
      view.rerender(
        <SearchScopeHarness
          services={changeKind === "services-only" ? secondServices : firstServices}
          userId={changeKind === "user-only" ? "search-user-b" : "search-user-a"}
          selectedSeries={changeKind === "series-only" ? secondSeries : series}
        />,
      );
      expect(observations.at(-1)).toEqual({ query: null, oldCard: false });
      expect(await screen.findByRole("heading", { name: "顾行舟" })).toBeInTheDocument();

      inspectNextCommit = true;
      view.rerender(
        <SearchScopeHarness services={firstServices} userId="search-user-a" selectedSeries={series} />,
      );
      expect(observations.at(-1)).toEqual({ query: null, oldCard: false });
      expect(await screen.findByRole("heading", { name: "阿岚" })).toBeInTheDocument();
      expect(screen.getByRole("searchbox", { name: "搜索角色素材" })).toHaveValue("");
      const readsBeforeReplay = listCharactersA.mock.calls.length + listCharactersB.mock.calls.length;
      act(() => oldHandler({ currentTarget: { value: "林编" } }));
      expect(screen.getByRole("searchbox", { name: "搜索角色素材" })).toHaveValue("");
      expect(screen.getByRole("article", { name: "阿岚素材卡片" })).toBeInTheDocument();
      expect(listCharactersA.mock.calls.length + listCharactersB.mock.calls.length).toBe(readsBeforeReplay);
    },
  );

  it("hides a nonempty query and usage panel on the first layout commit of a new navigation epoch", async () => {
    const user = userEvent.setup();
    const lin = character({ id: "nav-layout-lin", name: "林岚", aliases: ["阿岚"] });
    const gu = character({ id: "nav-layout-gu", name: "顾行舟" });
    const listCharacters = vi.fn(async () => [lin, gu]);
    const listChapters = vi.fn(async () => [chapter("林岚关联章节", "林岚关联镜头", lin.id)]);
    const services = makeServices({ listCharacters, listChapters });
    const observations: Array<{ queryVisible: boolean; oldCardVisible: boolean; usageVisible: boolean }> = [];
    let inspectNextCommit = false;
    const observeCommit = () => {
      if (!inspectNextCommit) {
        return;
      }
      inspectNextCommit = false;
      observations.push({
        queryVisible: screen.queryByRole("searchbox", { name: "搜索角色素材" }) !== null,
        oldCardVisible: screen.queryByRole("article", { name: "林岚素材卡片" }) !== null,
        usageVisible: screen.queryByRole("region", { name: "关联镜头" }) !== null,
      });
    };
    const nextIntent: AssetNavigationIntent = {
      userId: "layout-nav-user",
      services,
      seriesId: series.id,
      category: "characters",
      assetId: gu.id,
      navigationEpoch: 404,
    };
    const onConsumed = vi.fn();
    const renderIntent = (intent: AssetNavigationIntent | null) => (
      <>
        <AssetLibrary
          navigationIntent={intent}
          onBack={vi.fn()}
          onNavigationConsumed={onConsumed}
          onUnauthorized={vi.fn()}
          series={series}
          services={services}
          userId="layout-nav-user"
        />
        <LayoutCommitObserver onCommit={observeCommit} />
      </>
    );

    const view = render(renderIntent(null));
    expect(await screen.findByRole("article", { name: "林岚素材卡片" })).toBeInTheDocument();
    await user.type(screen.getByRole("searchbox", { name: "搜索角色素材" }), "阿岚");
    expect(await screen.findByText("显示 1 / 2 项素材")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看关联镜头：林岚" }));
    expect(await screen.findByRole("region", { name: "关联镜头" })).toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(1);
    expect(listChapters).toHaveBeenCalledTimes(1);

    inspectNextCommit = true;
    view.rerender(renderIntent(nextIntent));
    expect(observations.at(-1)).toEqual({
      queryVisible: false,
      oldCardVisible: false,
      usageVisible: false,
    });
    const targetCard = await screen.findByRole("article", { name: "顾行舟素材卡片" });
    await waitFor(() => expect(targetCard).toHaveFocus());
    expect(screen.getByRole("searchbox", { name: "搜索角色素材" })).toHaveValue("");
    expect(await screen.findByText("显示 2 / 2 项素材")).toBeInTheDocument();
    expect(onConsumed).toHaveBeenCalledExactlyOnceWith(nextIntent.navigationEpoch);
    expect(listCharacters).toHaveBeenCalledTimes(2);
    expect(listChapters).toHaveBeenCalledTimes(1);
  });

  it("clears the query on category changes and explicit rereads, while a repeated category click preserves it", async () => {
    const user = userEvent.setup();
    const listCharacters = vi.fn(async () => [
      character({ id: "search-reset-a", name: "阿岚" }),
      character({ id: "search-reset-b", name: "顾行舟" }),
    ]);
    const listScenes = vi.fn(async () => [
      scene({ id: "scene-a", title: "档案馆地下室" }),
      scene({ id: "scene-b", title: "旧城仓库" }),
    ]);
    const refreshedProps = deferred<Prop[]>();
    const listProps = vi.fn()
      .mockResolvedValueOnce([
        prop({ id: "prop-a", name: "旧铜钥匙" }),
        prop({ id: "prop-b", name: "蓝印信封", aliases: [] }),
      ])
      .mockReturnValueOnce(refreshedProps.promise);
    const services = makeServices({ listCharacters, listScenes, listProps });
    const firstUnauthorized = vi.fn();
    const { rerender } = renderLibrary(services, firstUnauthorized);

    const roleSearch = await screen.findByRole("searchbox", { name: "搜索角色素材" });
    await user.type(roleSearch, "阿岚");
    expect(await screen.findByText("显示 1 / 2 项素材")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^角色/ }));
    expect(screen.getByRole("searchbox", { name: "搜索角色素材" })).toHaveValue("阿岚");
    expect(listCharacters).toHaveBeenCalledTimes(1);

    const secondUnauthorized = vi.fn();
    rerender(
      <AssetLibrary
        onBack={vi.fn()}
        onUnauthorized={secondUnauthorized}
        series={series}
        services={services}
        userId="demo-user"
      />,
    );
    expect(screen.getByRole("searchbox", { name: "搜索角色素材" })).toHaveValue("阿岚");
    expect(listCharacters).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "场景" }));
    const sceneSearch = await screen.findByRole("searchbox", { name: "搜索场景素材" });
    expect(sceneSearch).toHaveValue("");
    expect(await screen.findByText("显示 2 / 2 项素材")).toBeInTheDocument();
    expect(listScenes).toHaveBeenCalledTimes(1);

    await user.type(sceneSearch, "地下");
    expect(await screen.findByText("显示 1 / 2 项素材")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "道具" }));
    let propSearch = await screen.findByRole("searchbox", { name: "搜索道具素材" });
    expect(propSearch).toHaveValue("");
    expect(await screen.findByText("显示 2 / 2 项素材")).toBeInTheDocument();

    await user.type(propSearch, "钥匙");
    expect(await screen.findByText("显示 1 / 2 项素材")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新读取" }));
    await waitFor(() => expect(listProps).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("searchbox", { name: "搜索道具素材" })).not.toBeInTheDocument();
    await act(async () => {
      refreshedProps.resolve([
        prop({ id: "prop-a", name: "旧铜钥匙" }),
        prop({ id: "prop-b", name: "蓝印信封", aliases: [] }),
      ]);
      await refreshedProps.promise;
    });
    propSearch = await screen.findByRole("searchbox", { name: "搜索道具素材" });
    expect(propSearch).toHaveValue("");
    expect(await screen.findByText("显示 2 / 2 项素材")).toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(1);
    expect(listScenes).toHaveBeenCalledTimes(1);
    expect(secondUnauthorized).not.toHaveBeenCalled();
  });

  it("uses a placeholder for an empty API name", async () => {
    renderLibrary(makeServices({
      listCharacters: vi.fn(async () => [character({ name: "" })]),
    }));

    expect(await screen.findByRole("heading", { name: "未命名角色" })).toBeInTheDocument();
  });

  it("keeps an empty list distinct and retries a forbidden request without clearing the session", async () => {
    const user = userEvent.setup();
    const listCharacters = vi.fn()
      .mockRejectedValueOnce(new ApiError("http", "forbidden", 403, "暂时没有访问权限。"))
      .mockResolvedValueOnce([]);
    const onUnauthorized = vi.fn();
    renderLibrary(makeServices({ listCharacters }), onUnauthorized);

    expect(await screen.findByRole("heading", { name: "没有访问权限" })).toBeInTheDocument();
    expect(screen.getByText("暂时没有访问权限。")).toBeInTheDocument();
    expect(screen.queryByRole("searchbox", { name: "搜索角色素材" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试读取" }));
    expect(await screen.findByRole("heading", { name: "还没有角色素材" })).toBeInTheDocument();
    expect(screen.queryByRole("searchbox", { name: "搜索角色素材" })).not.toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(listCharacters).toHaveBeenCalledTimes(2);
  });

  it("distinguishes membership limits from other forbidden responses", async () => {
    const listCharacters = vi.fn(async () => {
      throw new ApiError("http", "membership denied", 403, "会员资格已过期。");
    });
    renderLibrary(makeServices({ listCharacters }));

    expect(await screen.findByRole("heading", { name: "当前账号暂不可查看素材" })).toBeInTheDocument();
    expect(screen.getByText("会员资格已过期。")).toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(1);
  });

  it("logs out on a current asset 401", async () => {
    const onUnauthorized = vi.fn();
    const listCharacters = vi.fn(async () => {
      throw new ApiError("http", "expired", 401);
    });
    renderLibrary(makeServices({ listCharacters }), onUnauthorized);

    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  it("keeps the refresh control enabled while loading and ignores a stale 401 after the refreshed request succeeds", async () => {
    const user = userEvent.setup();
    const firstRequest = deferred<Character[]>();
    const secondRequest = deferred<Character[]>();
    const listCharacters = vi.fn()
      .mockReturnValueOnce(firstRequest.promise)
      .mockReturnValueOnce(secondRequest.promise);
    const onUnauthorized = vi.fn();
    renderLibrary(makeServices({ listCharacters }), onUnauthorized);

    expect(await screen.findByText("正在读取角色…")).toBeInTheDocument();
    expect(screen.queryByRole("searchbox", { name: "搜索角色素材" })).not.toBeInTheDocument();
    const refresh = screen.getByRole("button", { name: "重新读取" });
    expect(refresh).toBeEnabled();
    await user.click(refresh);
    await waitFor(() => expect(listCharacters).toHaveBeenCalledTimes(2));

    await act(async () => {
      secondRequest.resolve([character({ name: "当前请求结果" })]);
      await secondRequest.promise;
    });
    expect(await screen.findByRole("heading", { name: "当前请求结果" })).toBeInTheDocument();

    await act(async () => {
      firstRequest.reject(new ApiError("http", "expired", 401));
      await firstRequest.promise.catch(() => undefined);
    });
    expect(screen.getByRole("heading", { name: "当前请求结果" })).toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("ignores a late success from a category that is no longer selected", async () => {
    const user = userEvent.setup();
    const lateCharacters = deferred<Character[]>();
    const listCharacters = vi.fn(() => lateCharacters.promise);
    const listScenes = vi.fn(async () => [scene({ title: "当前场景" })]);
    const onUnauthorized = vi.fn();
    renderLibrary(makeServices({ listCharacters, listScenes }), onUnauthorized);

    expect(await screen.findByText("正在读取角色…")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "场景" }));
    expect(await screen.findByRole("heading", { name: "当前场景" })).toBeInTheDocument();
    await act(async () => {
      lateCharacters.resolve([character({ name: "不应显示的迟到角色" })]);
      await lateCharacters.promise;
    });
    expect(screen.getByRole("heading", { name: "当前场景" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "不应显示的迟到角色" })).not.toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("resets the current type's broken-image fallback when explicitly reloaded", async () => {
    const user = userEvent.setup();
    const image = "http://127.0.0.1:4175/media/character.png";
    const listCharacters = vi.fn(async () => [character({ image_url: image })]);
    const { container } = renderLibrary(makeServices({ listCharacters }));

    const firstImage = await screen.findByAltText("顾行舟图片");
    fireEvent.error(firstImage);
    expect(await screen.findByRole("img", { name: "顾行舟没有可显示的本地图片" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByAltText("顾行舟图片")).toHaveAttribute("src", image);
    expect(container.querySelectorAll("img")).toHaveLength(1);
    expect(listCharacters).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["userId", "success"],
    ["userId", "401"],
    ["services", "success"],
    ["services", "401"],
  ] as const)(
    "permanently rejects the old intent after a %s-only scope ABA when its old request later settles with %s",
    async (changedScope, lateResult) => {
      const staleRequest = deferred<Scene[]>();
      const target = scene({ id: "aba-target", title: "普通列表中的同 ID 场景" });
      const otherScopeScene = scene({ id: "other-scope-scene", title: "另一 scope 的场景" });
      const listScenesA = vi.fn()
        .mockReturnValueOnce(staleRequest.promise)
        .mockResolvedValueOnce([changedScope === "userId" ? otherScopeScene : target])
        .mockResolvedValue([target]);
      const servicesA = makeServices({ listScenes: listScenesA });
      const servicesB = makeServices({
        listScenes: vi.fn(async () => [otherScopeScene]),
      });
      const listScenesB = servicesB.listScenes;
      const onUnauthorized = vi.fn();
      const onConsumed = vi.fn();
      const onAbandoned = vi.fn();
      const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
      const scrollIntoView = vi.fn();
      Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
        configurable: true,
        value: scrollIntoView,
      });
      const oldIntent: AssetNavigationIntent = {
        userId: "user-a",
        services: servicesA,
        seriesId: series.id,
        category: "scenes",
        assetId: target.id,
        navigationEpoch: 110,
      };
      const renderWith = (
        currentUserId: string,
        currentServices: WorkspaceServices,
        intent: AssetNavigationIntent,
      ) => (
        <AssetLibrary
          navigationIntent={intent}
          onBack={vi.fn()}
          onNavigationAbandoned={onAbandoned}
          onNavigationConsumed={onConsumed}
          onUnauthorized={onUnauthorized}
          series={series}
          services={currentServices}
          userId={currentUserId}
        />
      );
      const view = render(renderWith("user-a", servicesA, oldIntent));

      await waitFor(() => expect(listScenesA).toHaveBeenCalledTimes(1));
      expect(await screen.findByText("正在读取场景…")).toBeInTheDocument();

      const movedUserId = changedScope === "userId" ? "user-b" : "user-a";
      const movedServices = changedScope === "services" ? servicesB : servicesA;
      view.rerender(renderWith(movedUserId, movedServices, oldIntent));
      if (changedScope === "services") {
        expect(await screen.findByRole("article", { name: "另一 scope 的场景素材卡片" })).toBeInTheDocument();
        expect(listScenesB).toHaveBeenCalledTimes(1);
      } else {
        expect(await screen.findByRole("article", { name: "另一 scope 的场景素材卡片" })).toBeInTheDocument();
      }

      view.rerender(renderWith("user-a", servicesA, oldIntent));
      const ordinaryCard = await screen.findByRole("article", { name: "普通列表中的同 ID 场景素材卡片" });
      expect(ordinaryCard).not.toHaveFocus();
      expect(screen.queryByText("已定位到对应素材")).not.toBeInTheDocument();
      expect(onConsumed).not.toHaveBeenCalled();
      expect(onAbandoned).not.toHaveBeenCalled();
      expect(listScenesA).toHaveBeenCalledTimes(changedScope === "userId" ? 3 : 2);

      await act(async () => {
        if (lateResult === "success") {
          staleRequest.resolve([scene({ id: target.id, title: "迟到的目标场景" })]);
          await staleRequest.promise;
        } else {
          staleRequest.reject(new ApiError("http", "expired", 401));
          await staleRequest.promise.catch(() => undefined);
        }
        await Promise.resolve();
      });
      expect(screen.getByRole("article", { name: "普通列表中的同 ID 场景素材卡片" })).not.toHaveFocus();
      expect(onConsumed).not.toHaveBeenCalled();
      expect(onAbandoned).not.toHaveBeenCalled();
      expect(onUnauthorized).not.toHaveBeenCalled();
      expect(scrollIntoView).not.toHaveBeenCalled();

      const freshIntent: AssetNavigationIntent = { ...oldIntent, navigationEpoch: 111 };
      view.rerender(renderWith("user-a", servicesA, freshIntent));
      const freshTarget = await screen.findByRole("article", { name: "普通列表中的同 ID 场景素材卡片" });
      await waitFor(() => expect(freshTarget).toHaveFocus());
      expect(listScenesA).toHaveBeenCalledTimes(changedScope === "userId" ? 4 : 3);
      expect(onConsumed).toHaveBeenCalledExactlyOnceWith(freshIntent.navigationEpoch);
      expect(onAbandoned).not.toHaveBeenCalled();
      expect(onUnauthorized).not.toHaveBeenCalled();
      expect(scrollIntoView).toHaveBeenCalledExactlyOnceWith({ behavior: "auto", block: "center" });

      if (originalScrollIntoView === undefined) {
        Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
      } else {
        Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
          configurable: true,
          value: originalScrollIntoView,
        });
      }
    },
  );

  it("does not request remote or non-loopback images", async () => {
    const services = makeServices({
      listCharacters: vi.fn(async () => [character({ image_url: "https://assets.example.invalid/private.jpg" })]),
    });
    renderLibrary(services);

    expect(await screen.findByRole("heading", { name: "顾行舟" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "顾行舟没有可显示的本地图片" })).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "顾行舟图片" })).not.toBeInTheDocument();
  });
});
