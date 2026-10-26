import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createDemoServices, createApiServices, type WorkspaceServices } from "../../shared/api/services";
import type { FusedAsset, Series } from "../../shared/api/contracts";
import { API_TOKEN_KEY } from "../../shared/api/storage";
import { demoSeries } from "../series/demoSeries";
import { FusedAssetsPage } from "./FusedAssetsPage";

const apiBaseUrl = "http://127.0.0.1:4175/api";

function series(id: string, name = "雾港来信"): Series {
  return { ...demoSeries[0]!, id, name, can_enter: true };
}

function fusedAsset(overrides: Partial<FusedAsset> = {}): FusedAsset {
  return {
    id: "fused-asset-1",
    series_id: "series-1",
    member_type: "prop",
    members: [{ kind: "asset", id: "prop-1", name: "旧车票", private_prompt: "不可展示的提示词" }],
    name: "旧车票融合",
    image_url: "/media/fused/asset-1.png",
    size: "1024x1024",
    status: "completed",
    error_message: null,
    created_at: "2026-10-26T00:00:00Z",
    updated_at: "2026-10-26T00:00:00Z",
    ...overrides,
  };
}

function renderPage(services: WorkspaceServices, currentSeries = series("series-1")) {
  return render(
    <FusedAssetsPage
      onBack={vi.fn()}
      onUnauthorized={vi.fn()}
      series={currentSeries}
      services={services}
      sessionGeneration={1}
      userId="fixture-user"
    />,
  );
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => { resolve = complete; });
  return { promise, resolve };
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("FusedAssetsPage", () => {
  it("loads the real fused-assets route, previews only safe media, and rereads a name-only update", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    window.localStorage.setItem(API_TOKEN_KEY, "fused-assets-session");
    let current = fusedAsset({ series_id: "series-1", name: "   " });
    let exists = true;
    const calls: Array<{ url: URL; init: RequestInit }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input));
      calls.push({ url, init });
      if (init.method === "GET") return jsonResponse(exists ? [current] : []);
      if (init.method === "PUT") {
        const body = JSON.parse(String(init.body)) as { name: string };
        current = fusedAsset({ series_id: "series-1", name: body.name });
        return jsonResponse(current);
      }
      if (init.method === "DELETE") {
        exists = false;
        return new Response(null, { status: 204 });
      }
      throw new Error("Unexpected HTTP method.");
    });
    const services = createApiServices(apiBaseUrl, { storage: window.localStorage, fetcher });

    renderPage(services);

    const card = await screen.findByRole("article", { name: "未命名融合素材融合素材" });
    expect(within(card).getByRole("img", { name: "未命名融合素材图片" }))
      .toHaveAttribute("src", "http://127.0.0.1:4175/media/fused/asset-1.png");
    expect(within(card).getByText("旧车票")).toBeInTheDocument();
    expect(within(card).queryByText("不可展示的提示词")).not.toBeInTheDocument();
    expect(within(card).getByText("已完成")).toBeInTheDocument();

    await user.click(within(card).getByRole("button", { name: "改名" }));
    const nameInput = within(card).getByRole("textbox", { name: "素材名称" });
    await user.clear(nameInput);
    await user.type(nameInput, "更新后的融合素材");
    await user.click(within(card).getByRole("button", { name: "保存名称" }));

    expect(await screen.findByText("名称已保存，并已回读当前剧集目录。")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "更新后的融合素材" })).toBeInTheDocument();
    expect(JSON.parse(String(calls[1]?.init.body))).toEqual({ name: "更新后的融合素材" });
    await user.click(within(card).getByRole("button", { name: "删除" }));
    expect(await screen.findByText("融合素材已删除，并已从当前剧集目录确认移除。")).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "更新后的融合素材融合素材" })).not.toBeInTheDocument();
    expect(screen.getByText("当前剧集还没有融合素材。")).toBeInTheDocument();
    expect(calls.map(({ url, init }) => [init.method, url.pathname])).toEqual([
      ["GET", "/api/series/series-1/fused-assets"],
      ["PUT", "/api/fused-assets/fused-asset-1"],
      ["GET", "/api/series/series-1/fused-assets"],
      ["DELETE", "/api/fused-assets/fused-asset-1"],
      ["GET", "/api/series/series-1/fused-assets"],
    ]);
    expect(calls[3]?.init.body).toBeUndefined();
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("同时清理本剧集中对此融合素材的引用"));
  });

  it("reconciles unknown rename and delete outcomes with only list reads and no automatic write retry", async () => {
    const user = userEvent.setup();
    const listFusedAssets = vi.fn(async () => [fusedAsset()]);
    const updateFusedAsset = vi.fn(async () => { throw new TypeError("connection lost"); });
    const deleteFusedAsset = vi.fn(async () => { throw new TypeError("connection lost"); });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      apiBaseUrl,
      listFusedAssets,
      updateFusedAsset,
      deleteFusedAsset,
    };

    renderPage(services);
    const card = await screen.findByRole("article", { name: "旧车票融合融合素材" });
    await user.click(within(card).getByRole("button", { name: "改名" }));
    const nameInput = within(card).getByRole("textbox", { name: "素材名称" });
    await user.clear(nameInput);
    await user.type(nameInput, "待核对名称");
    await user.click(within(card).getByRole("button", { name: "保存名称" }));

    expect(await screen.findByText("改名结果未知，已只读刷新当前剧集目录。请按当前名称核对；系统不会自动重试。"))
      .toBeInTheDocument();
    expect(listFusedAssets).toHaveBeenCalledTimes(2);
    expect(updateFusedAsset).toHaveBeenCalledTimes(1);
    expect(updateFusedAsset).toHaveBeenCalledWith(
      "series-1",
      "fused-asset-1",
      { name: "待核对名称" },
      expect.any(AbortSignal),
    );

    await user.click(within(card).getByRole("button", { name: "删除" }));
    expect(await screen.findByText("删除结果未知，只读刷新后目录仍显示此素材。请核对后再决定下一步；系统不会自动重复删除。"))
      .toBeInTheDocument();
    expect(listFusedAssets).toHaveBeenCalledTimes(3);
    expect(updateFusedAsset).toHaveBeenCalledTimes(1);
    expect(deleteFusedAsset).toHaveBeenCalledTimes(1);
  });

  it("previews public HTTPS media without a referrer and rejects executable image URLs", async () => {
    const publicImage = fusedAsset({
      id: "public-image",
      name: "公网融合图",
      image_url: "https://cdn.example.test/media/public.png?signature=fixture",
    });
    const unsafeImage = fusedAsset({
      id: "unsafe-image",
      name: "不安全素材",
      image_url: "javascript:alert(1)",
    });
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      apiBaseUrl,
      listFusedAssets: async () => [publicImage, unsafeImage],
    };

    renderPage(services);

    const publicCard = await screen.findByRole("article", { name: "公网融合图融合素材" });
    const image = within(publicCard).getByRole("img", { name: "公网融合图图片" });
    expect(image).toHaveAttribute("src", "https://cdn.example.test/media/public.png?signature=fixture");
    expect(image).toHaveAttribute("referrerpolicy", "no-referrer");
    const unsafeCard = screen.getByRole("article", { name: "不安全素材融合素材" });
    expect(within(unsafeCard).queryByRole("img", { name: "不安全素材图片" })).not.toBeInTheDocument();
    expect(within(unsafeCard).getByRole("img", { name: "不安全素材没有可显示的图片" })).toBeInTheDocument();
  });

  it("ignores a late directory response from the previous series scope", async () => {
    const firstDirectory = deferred<FusedAsset[]>();
    const listFusedAssets = vi.fn((seriesId: string) => (
      seriesId === "series-1"
        ? firstDirectory.promise
        : Promise.resolve([fusedAsset({ id: "series-2-asset", series_id: "series-2", name: "新范围素材" })])
    ));
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      apiBaseUrl,
      listFusedAssets,
    };
    const initialSeries = series("series-1");
    const view = renderPage(services, initialSeries);

    await waitFor(() => expect(listFusedAssets).toHaveBeenCalledTimes(1));
    view.rerender(
      <FusedAssetsPage
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
        series={series("series-2", "第七码头")}
        services={services}
        sessionGeneration={1}
        userId="fixture-user"
      />,
    );

    expect(await screen.findByRole("article", { name: "新范围素材融合素材" })).toBeInTheDocument();
    await act(async () => {
      firstDirectory.resolve([fusedAsset({ name: "旧范围迟到素材" })]);
      await firstDirectory.promise;
    });
    expect(screen.queryByText("旧范围迟到素材")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "新范围素材" })).toBeInTheDocument();
    expect(listFusedAssets).toHaveBeenCalledTimes(2);
  });
});
