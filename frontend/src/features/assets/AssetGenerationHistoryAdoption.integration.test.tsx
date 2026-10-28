// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createApiServices } from "../../shared/api/services";
import { API_TOKEN_KEY, API_USER_KEY, type StorageLike } from "../../shared/api/storage";
import { demoSeries } from "../series/demoSeries";
import { AssetLibrary } from "./AssetLibrary";

const series = demoSeries[0]!;
const actorId = series.user_id;
const apiBaseUrl = "http://127.0.0.1:4175/api";
const chapterId = "history-adoption-chapter";
const mediaUrl = "http://127.0.0.1:4175/uploads/generated/adopted.png";
const videoUrl = "http://127.0.0.1:4175/uploads/generated/adopted.mp4";
const previousImageUrl = "https://cdn.example.test/assets/original.png";

type AssetCategory = "characters" | "scenes" | "props";

const categories: ReadonlyArray<{
  readonly category: AssetCategory;
  readonly label: string;
  readonly singular: "character" | "scene" | "prop";
  readonly assetId: string;
}> = [
  { category: "characters", label: "角色", singular: "character", assetId: "history-adoption-character" },
  { category: "scenes", label: "场景", singular: "scene", assetId: "history-adoption-scene" },
  { category: "props", label: "道具", singular: "prop", assetId: "history-adoption-prop" },
];

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function assetRecord(category: AssetCategory, id: string, imageUrl: string | null): Record<string, unknown> {
  const common = {
    id,
    series_id: series.id,
    description: null,
    image_url: imageUrl,
    aliases: [],
    canonical_key: null,
    created_at: "2026-10-26T04:00:00Z",
    updated_at: "2026-10-26T04:00:00Z",
  };
  switch (category) {
    case "characters":
      return {
        ...common,
        name: "历史角色",
        gender: null,
        age: null,
        role: null,
        appearance: null,
        audio_url: null,
        voice_ref: null,
      };
    case "scenes": return { ...common, title: "历史场景" };
    case "props": return { ...common, name: "历史道具" };
  }
}

function makeFixture(options: {
  readonly adoptionUrl?: string;
  readonly adoptionMode?: "image" | "video";
  readonly malformedUpdateReceipt?: boolean;
  readonly timeoutStatus?: number;
} = {}) {
  const adoptionMode = options.adoptionMode ?? "image";
  const adoptionUrl = options.adoptionUrl ?? (adoptionMode === "image" ? mediaUrl : videoUrl);
  const storage = new MemoryStorage();
  storage.setItem(API_TOKEN_KEY, "history-adoption-token");
  storage.setItem(API_USER_KEY, JSON.stringify({ id: actorId }));
  const assets = new Map<AssetCategory, Record<string, unknown>>(
    categories.map(({ category, assetId }) => [category, assetRecord(category, assetId, previousImageUrl)]),
  );
  const messages = new Map(categories.map((category) => [category.singular, {
    id: `history-message-${category.singular}`,
    chapter_id: chapterId,
    asset_type: category.singular,
    asset_id: category.assetId,
    chat_mode: adoptionMode,
    role: "assistant",
    content: adoptionUrl,
    model_name: `synthetic-${adoptionMode}-model`,
    created_at: "2026-10-26T04:00:00Z",
  }]));
  const calls: Array<{ readonly url: string; readonly method: string; readonly body: unknown }> = [];
  let nextAssetRead: { readonly promise: Promise<void>; readonly requested: () => void } | null = null;
  function deferNextAssetRead(): { readonly requested: Promise<void>; release(): void } {
    let resolveRead: () => void = () => undefined;
    let resolveRequested: () => void = () => undefined;
    const promise = new Promise<void>((resolve) => { resolveRead = resolve; });
    const requested = new Promise<void>((resolve) => { resolveRequested = resolve; });
    nextAssetRead = { promise, requested: resolveRequested };
    return { requested, release: resolveRead };
  }
  const fetcher: typeof fetch = async (input, init = {}) => {
    const url = new URL(input instanceof URL ? input.href : String(input));
    const path = url.pathname.replace(/^\/api(?=\/)/u, "");
    const method = init.method ?? "GET";
    let body: unknown = null;
    if (typeof init.body === "string") body = JSON.parse(init.body) as unknown;
    calls.push({ url: url.href, method, body });

    if (path === `/series/${series.id}/chapters` && method === "GET") {
      return jsonResponse([{
        id: chapterId,
        series_id: series.id,
        title: "第一章",
        content: [],
        order: 1,
        created_at: "2026-10-26T04:00:00Z",
        updated_at: "2026-10-26T04:00:00Z",
        lock: null,
      }]);
    }

    const category = categories.find(({ category: name }) => path === `/series/${series.id}/${name}`
      || path.startsWith(`/${name}/`));
    if (category !== undefined) {
      const current = assets.get(category.category)!;
      if (path === `/series/${series.id}/${category.category}` && method === "GET") {
        const heldRead = nextAssetRead;
        nextAssetRead = null;
        if (heldRead !== null) {
          heldRead.requested();
          await heldRead.promise;
        }
        return jsonResponse([current]);
      }
      if (path === `/${category.category}/${category.assetId}` && method === "PUT") {
        if (options.timeoutStatus !== undefined) {
          return jsonResponse({ detail: "write result unknown" }, options.timeoutStatus);
        }
        if (typeof body !== "object" || body === null || Array.isArray(body)) {
          throw new Error("Expected an image_url update object.");
        }
        const fields = Object.keys(body as Record<string, unknown>);
        if (fields.length !== 1 || fields[0] !== "image_url") {
          throw new Error(`Unexpected asset update fields: ${fields.join(",")}`);
        }
        const updated = { ...current, image_url: (body as { image_url: string }).image_url };
        assets.set(category.category, updated);
        if (options.malformedUpdateReceipt) return jsonResponse({ ...updated, id: "unexpected-asset-id" });
        return jsonResponse(updated);
      }
    }

    if (path === `/chapters/${chapterId}/asset-chat-messages`) {
      const selected = categories.find((candidate) => candidate.singular === url.searchParams.get("asset_type"));
      if (method !== "GET" || selected === undefined || url.searchParams.get("asset_id") !== selected.assetId
        || !["image", "video"].includes(url.searchParams.get("chat_mode") ?? "")) {
        throw new Error(`Unexpected history request: ${method} ${url.href}`);
      }
      const message = messages.get(selected.singular)!;
      return jsonResponse(message.chat_mode === url.searchParams.get("chat_mode") ? [message] : []);
    }

    throw new Error(`Unexpected request: ${method} ${url.href}`);
  };
  const services = createApiServices(apiBaseUrl, { storage, fetcher, timeoutMs: 1_000 });
  const historyPort = services.assetGenerationHistory;
  if (historyPort === undefined) throw new Error("The API services did not create the history port.");
  const rendered = render(
    <AssetLibrary
      generationHistoryPort={historyPort}
      onBack={() => undefined}
      onUnauthorized={vi.fn()}
      series={series}
      services={services}
      userId={actorId}
    />,
  );
  return { assets, calls, deferNextAssetRead, messages, unmount: rendered.unmount };
}

async function openCategoryHistory(category: typeof categories[number], mode: "image" | "video" = "image"): Promise<void> {
  fireEvent.click(within(screen.getByRole("group", { name: "素材分类" })).getByRole("button", { name: new RegExp(category.label) }));
  fireEvent.click(await screen.findByRole("button", { name: "生成历史" }));
  const chapterSelect = await screen.findByLabelText("选择历史所属章节");
  await waitFor(() => expect(chapterSelect).not.toBeDisabled());
  fireEvent.change(chapterSelect, { target: { value: chapterId } });
  if (mode === "video") fireEvent.click(screen.getByRole("button", { name: "视频" }));
  await screen.findByRole("button", {
    name: mode === "image" ? `设为${category.label}图` : `采用此视频作为${category.label}素材`,
  });
}

afterEach(() => cleanup());

describe("AssetGenerationHistory media adoption", () => {
  it("updates character, scene, and prop images through their typed APIs and confirms each by a fresh directory read", async () => {
    const { assets, calls } = makeFixture();

    for (const category of categories) {
      await openCategoryHistory(category);
      fireEvent.click(screen.getByRole("button", { name: `设为${category.label}图` }));
      await screen.findByRole("group", { name: "确认设置素材图片" });
      expect(calls.filter((call) => call.method === "PUT")).toHaveLength(categories.indexOf(category));
      fireEvent.click(screen.getByRole("button", { name: `确认设为${category.label}图` }));
      await screen.findByText("素材图片已保存，并由独立目录读取确认。");

      expect(assets.get(category.category)?.image_url).toBe(mediaUrl);
      const assetName = category.category === "scenes" ? "历史场景" : category.category === "props" ? "历史道具" : "历史角色";
      const assetPath = `/api/series/${series.id}/${category.category}`;
      const categoryCalls = calls.filter((call) => new URL(call.url).pathname === assetPath);
      const putIndex = calls.findIndex((call) => new URL(call.url).pathname === `/api/${category.category}/${category.assetId}` && call.method === "PUT");
      expect(putIndex).toBeGreaterThanOrEqual(0);
      expect(categoryCalls.some((call) => calls.indexOf(call) > putIndex)).toBe(true);
      expect(calls[putIndex]?.body).toEqual({ image_url: mediaUrl });

      fireEvent.click(screen.getByRole("button", { name: "关闭生成历史" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(await screen.findByAltText(`${assetName}图片`)).toHaveAttribute("src", mediaUrl);
    }
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(3);
  });

  it("adopts video history for character, scene, and prop assets and confirms each by a fresh directory read", async () => {
    const { assets, calls } = makeFixture({ adoptionMode: "video" });

    for (const category of categories) {
      await openCategoryHistory(category, "video");
      fireEvent.click(screen.getByRole("button", { name: `采用此视频作为${category.label}素材` }));
      await screen.findByRole("group", { name: "确认采用历史视频" });
      expect(calls.filter((call) => call.method === "PUT")).toHaveLength(categories.indexOf(category));
      fireEvent.click(screen.getByRole("button", { name: "确认采用此视频" }));
      await screen.findByText("视频已保存，并由独立目录读取确认。");

      expect(assets.get(category.category)?.image_url).toBe(videoUrl);
      const assetPath = `/api/series/${series.id}/${category.category}`;
      const putPath = `/api/${category.category}/${category.assetId}`;
      const putIndex = calls.findIndex((call) => new URL(call.url).pathname === putPath && call.method === "PUT");
      expect(putIndex).toBeGreaterThanOrEqual(0);
      expect(calls[putIndex]?.body).toEqual({ image_url: videoUrl });
      expect(calls.some((call, index) => index > putIndex
        && new URL(call.url).pathname === assetPath && call.method === "GET")).toBe(true);

      fireEvent.click(screen.getByRole("button", { name: "关闭生成历史" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    }
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(3);
  });

  it("keeps video writes single-shot after an uncertain response and only rereads the asset", async () => {
    const { assets, calls } = makeFixture({ adoptionMode: "video", timeoutStatus: 408 });
    const category = categories[0]!;
    await openCategoryHistory(category, "video");
    fireEvent.click(screen.getByRole("button", { name: `采用此视频作为${category.label}素材` }));
    await screen.findByRole("group", { name: "确认采用历史视频" });

    fireEvent.click(screen.getByRole("button", { name: "确认采用此视频" }));
    await screen.findByText("保存请求已发出但结果未知；已停止自动操作，请只读核对素材目录。");
    const putIndex = calls.findIndex((call) => call.method === "PUT");
    expect(putIndex).toBeGreaterThanOrEqual(0);
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(1);
    expect(calls.slice(putIndex + 1).some((call) => new URL(call.url).pathname === `/api/series/${series.id}/characters`
      && call.method === "GET")).toBe(true);
    expect(assets.get(category.category)?.image_url).toBe(previousImageUrl);
  });

  it("does not continue a pending video adoption after its history dialog owner closes", async () => {
    const { calls, deferNextAssetRead, unmount } = makeFixture({ adoptionMode: "video" });
    const category = categories[0]!;
    await openCategoryHistory(category, "video");
    const heldRead = deferNextAssetRead();
    fireEvent.click(screen.getByRole("button", { name: `采用此视频作为${category.label}素材` }));
    await heldRead.requested;

    unmount();
    heldRead.release();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(calls.filter((call) => call.method === "PUT")).toHaveLength(0));
  });

  it("does not overwrite an image changed while the confirmation is open", async () => {
    const { assets, calls, messages } = makeFixture();
    const category = categories[0]!;
    await openCategoryHistory(category);
    fireEvent.click(screen.getByRole("button", { name: `设为${category.label}图` }));
    await screen.findByRole("group", { name: "确认设置素材图片" });
    assets.set(category.category, { ...assets.get(category.category)!, image_url: "https://cdn.example.test/assets/newer.png" });

    fireEvent.click(screen.getByRole("button", { name: `确认设为${category.label}图` }));
    await screen.findByText("当前素材图片已变化，未覆盖新图片。请重新打开历史后确认。");
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(0);
    expect(assets.get(category.category)?.image_url).toBe("https://cdn.example.test/assets/newer.png");

    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "重新读取" }));
    await screen.findByRole("button", { name: `设为${category.label}图` });
    fireEvent.click(screen.getByRole("button", { name: `设为${category.label}图` }));
    await screen.findByRole("group", { name: "确认设置素材图片" });
    messages.set(category.singular, { ...messages.get(category.singular)!, content: "https://media.example.test/generated/changed.png" });
    fireEvent.click(screen.getByRole("button", { name: `确认设为${category.label}图` }));
    await screen.findByText("原生成记录已变化，未修改素材图片。");
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(0);
    expect(assets.get(category.category)?.image_url).toBe("https://cdn.example.test/assets/newer.png");
  });

  it("treats HTTP 408 as unknown and only rereads the asset without another write", async () => {
    const { assets, calls } = makeFixture({ timeoutStatus: 408 });
    const category = categories[0]!;
    await openCategoryHistory(category);
    fireEvent.click(screen.getByRole("button", { name: `设为${category.label}图` }));
    await screen.findByRole("group", { name: "确认设置素材图片" });

    fireEvent.click(screen.getByRole("button", { name: `确认设为${category.label}图` }));
    await screen.findByText("保存请求已发出但结果未知；已停止自动操作，请只读核对素材目录。");
    const putIndex = calls.findIndex((call) => call.method === "PUT");
    expect(putIndex).toBeGreaterThanOrEqual(0);
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(1);
    expect(calls.slice(putIndex + 1).some((call) => new URL(call.url).pathname === `/api/series/${series.id}/characters`)).toBe(true);
    expect(assets.get(category.category)?.image_url).toBe(previousImageUrl);
  });

  it("rereads the asset after a successful write with an unrecognized update receipt", async () => {
    const { assets, calls } = makeFixture({ malformedUpdateReceipt: true });
    const category = categories[0]!;
    await openCategoryHistory(category);
    fireEvent.click(screen.getByRole("button", { name: `设为${category.label}图` }));
    await screen.findByRole("group", { name: "确认设置素材图片" });

    fireEvent.click(screen.getByRole("button", { name: `确认设为${category.label}图` }));
    await screen.findByText("素材图片已保存，并由独立目录读取确认。");
    const putIndex = calls.findIndex((call) => call.method === "PUT");
    expect(putIndex).toBeGreaterThanOrEqual(0);
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(1);
    expect(calls.slice(putIndex + 1).some((call) => new URL(call.url).pathname === `/api/series/${series.id}/characters`)).toBe(true);
    expect(assets.get(category.category)?.image_url).toBe(mediaUrl);
  });

  it("renders an adopted public HTTPS image on its asset card", async () => {
    const adoptionUrl = "https://media.example.test/generated/adopted.png";
    const { assets, calls } = makeFixture({ adoptionUrl });
    const category = categories[0]!;
    await openCategoryHistory(category);
    fireEvent.click(screen.getByRole("button", { name: `设为${category.label}图` }));
    await screen.findByRole("group", { name: "确认设置素材图片" });

    fireEvent.click(screen.getByRole("button", { name: `确认设为${category.label}图` }));
    await screen.findByText("素材图片已保存，并由独立目录读取确认。");
    expect(assets.get(category.category)?.image_url).toBe(adoptionUrl);
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "关闭生成历史" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const image = await screen.findByAltText("历史角色图片");
    expect(image).toHaveAttribute("src", adoptionUrl);
    expect(image).toHaveAttribute("referrerpolicy", "no-referrer");
  });
});
