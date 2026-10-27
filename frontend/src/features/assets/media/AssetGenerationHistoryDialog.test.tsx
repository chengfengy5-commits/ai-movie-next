// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Chapter } from "../../../shared/api/contracts";
import type { AssetGenerationHistoryMessage } from "../../../shared/api/assetGenerationHistory";
import { createApiServices } from "../../../shared/api/services";
import { API_TOKEN_KEY, API_USER_KEY, clearApiSession, type StorageLike } from "../../../shared/api/storage";
import { AssetGenerationHistoryDialog } from "./AssetGenerationHistoryDialog";

const actorId = "history-actor-1001";
const seriesId = "history-series-1001";
const chapterId = "history-chapter-1001";
const assetId = "history-asset-1001";
const apiBaseUrl = "http://127.0.0.1:4175/api";
const originalUrl = "https://media.example.test/generated/portrait.png";
const hostedUrl = "https://cdn.example.test/generated/portrait.png";

const chapter: Chapter = {
  id: chapterId,
  series_id: seriesId,
  title: "第一章",
  content: [],
  order: 1,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
  lock: null,
};

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

function message(content: string): AssetGenerationHistoryMessage {
  return {
    id: "history-message-1001",
    chapter_id: chapterId,
    asset_type: "character",
    asset_id: assetId,
    chat_mode: "image",
    role: "assistant",
    content,
    model_name: "test-image-model",
    created_at: "2026-10-26T04:00:00Z",
  };
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function responseWithUrl(response: Response, url: string): Response {
  Object.defineProperty(response, "url", { configurable: true, value: url });
  return response;
}

const originalCreateObjectUrl = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const originalRevokeObjectUrl = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");

beforeEach(() => {
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:history-preview") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
});

afterEach(() => {
  cleanup();
  if (originalCreateObjectUrl === undefined) delete (URL as unknown as { createObjectURL?: unknown }).createObjectURL;
  else Object.defineProperty(URL, "createObjectURL", originalCreateObjectUrl);
  if (originalRevokeObjectUrl === undefined) delete (URL as unknown as { revokeObjectURL?: unknown }).revokeObjectURL;
  else Object.defineProperty(URL, "revokeObjectURL", originalRevokeObjectUrl);
});

function renderHistory(
  fetcher: typeof fetch,
  options: { readonly onUnauthorized?: () => void; readonly isCurrent?: () => boolean } = {},
) {
  const storage = new MemoryStorage();
  storage.setItem(API_TOKEN_KEY, "captured-history-token");
  storage.setItem(API_USER_KEY, JSON.stringify({ id: actorId }));
  const services = createApiServices(apiBaseUrl, {
    storage,
    fetcher,
    timeoutMs: 1_000,
  });
  const port = services.assetGenerationHistory;
  if (port === undefined) throw new Error("The API services did not create the asset-generation-history port.");
  const listChapters = vi.fn(async () => [chapter]);
  const onUnauthorized = options.onUnauthorized ?? vi.fn();
  render(
    <AssetGenerationHistoryDialog
      actorId={actorId}
      assetId={assetId}
      assetName="林照"
      assetType="character"
      apiBaseUrl={apiBaseUrl}
      isCurrent={options.isCurrent ?? (() => true)}
      listChapters={listChapters}
      onClose={() => undefined}
      onUnauthorized={onUnauthorized}
      port={port}
      seriesId={seriesId}
      seriesName="旧城"
    />,
  );
  return { listChapters, storage, onUnauthorized };
}

function pngResponse(url: string): Response {
  const bytes = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x01]);
  return responseWithUrl(new Response(bytes, { headers: { "Content-Type": "image/png" } }), url);
}

describe("AssetGenerationHistoryDialog", () => {
  it("transfers and deletes the selected media record once, confirming both changes by rereading it", async () => {
    let currentMessage: AssetGenerationHistoryMessage | null = message(originalUrl);
    let historyReadCount = 0;
    const calls: Array<{ url: string; method: string; init: RequestInit }> = [];
    const fetcher: typeof fetch = async (input, init = {}) => {
      const url = input instanceof URL ? input.href : String(input);
      const method = init.method ?? "GET";
      calls.push({ url, method, init });
      if (url === `${apiBaseUrl}/chapters/${chapterId}/asset-chat-messages?asset_type=character&asset_id=${assetId}&chat_mode=image`) {
        historyReadCount += 1;
        return jsonResponse(currentMessage === null ? [] : [currentMessage]);
      }
      if (url === originalUrl || url === hostedUrl) return pngResponse(url);
      if (url === `${apiBaseUrl}/upload/retry-oss` && method === "POST") {
        return jsonResponse({ url: hostedUrl });
      }
      if (url === `${apiBaseUrl}/chapters/${chapterId}/asset-chat-messages/${currentMessage?.id}` && method === "PUT") {
        const body = JSON.parse(String(init.body)) as { content: string };
        currentMessage = message(body.content);
        return jsonResponse(currentMessage);
      }
      if (url === `${apiBaseUrl}/chapters/${chapterId}/asset-chat-messages/single/${message(originalUrl).id}` && method === "DELETE") {
        currentMessage = null;
        return new Response(null, { status: 204 });
      }
      throw new Error(`Unexpected request: ${method} ${url}`);
    };

    renderHistory(fetcher);
    fireEvent.change(await screen.findByLabelText("选择历史所属章节"), { target: { value: chapterId } });
    await screen.findByRole("button", { name: "转存到 OSS" });
    fireEvent.click(screen.getByRole("button", { name: "转存到 OSS" }));

    await waitFor(() => expect(currentMessage?.content).toBe(hostedUrl));
    await waitFor(() => expect(calls.filter((call) => call.url === `${apiBaseUrl}/upload/retry-oss` && call.method === "POST")).toHaveLength(1));
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(1);
    await waitFor(() => expect(historyReadCount).toBeGreaterThanOrEqual(5));
    expect(screen.getByText("已转存并通过独立读取确认原消息新地址。")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(currentMessage).toBeNull());
    await screen.findByText("当前章节暂无图片记录。");
    expect(calls.filter((call) => call.method === "DELETE")).toHaveLength(1);
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(1);
    expect(calls.filter((call) => call.url.includes("/asset-chat-messages?") && call.method === "GET").length).toBeGreaterThanOrEqual(6);
  });

  it.each([503, 408])("does not repeat or write after an uncertain OSS response (HTTP %s), and only rereads history", async (status) => {
    const currentMessage = message(originalUrl);
    const calls: Array<{ url: string; method: string }> = [];
    const fetcher: typeof fetch = async (input, init = {}) => {
      const url = input instanceof URL ? input.href : String(input);
      const method = init.method ?? "GET";
      calls.push({ url, method });
      if (url === `${apiBaseUrl}/chapters/${chapterId}/asset-chat-messages?asset_type=character&asset_id=${assetId}&chat_mode=image`) {
        return jsonResponse([currentMessage]);
      }
      if (url === originalUrl) return pngResponse(url);
      if (url === `${apiBaseUrl}/upload/retry-oss` && method === "POST") return jsonResponse({ error: "unknown" }, status);
      throw new Error(`Unexpected request: ${method} ${url}`);
    };

    renderHistory(fetcher);
    fireEvent.change(await screen.findByLabelText("选择历史所属章节"), { target: { value: chapterId } });
    await screen.findByRole("button", { name: "转存到 OSS" });
    fireEvent.click(screen.getByRole("button", { name: "转存到 OSS" }));

    await screen.findByText(/转存请求结果未知；原消息仍是原地址/);
    expect(calls.filter((call) => call.url === `${apiBaseUrl}/upload/retry-oss` && call.method === "POST")).toHaveLength(1);
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(0);
    expect(calls.filter((call) => call.method === "DELETE")).toHaveLength(0);
    expect(calls.filter((call) => call.url.includes("asset-chat-messages?") && call.method === "GET").length).toBeGreaterThanOrEqual(3);
    fireEvent.click(screen.getByRole("button", { name: "停止本地跟踪" }));
    expect(screen.queryByText(/转存请求结果未知；原消息仍是原地址/)).toBeNull();
  });

  it.each(["history-read", "transfer", "protected-preview"] as const)(
    "routes a current-session 401 from %s to the UI once",
    async (scenario) => {
      const imageUrl = scenario === "protected-preview"
        ? "http://127.0.0.1:4175/uploads/history-preview.png"
        : originalUrl;
      const currentMessage = message(imageUrl);
      let activeStorage: MemoryStorage | null = null;
      const onUnauthorized = vi.fn(() => {
        if (activeStorage !== null) clearApiSession(activeStorage);
      });
      const historyUrl = `${apiBaseUrl}/chapters/${chapterId}/asset-chat-messages?asset_type=character&asset_id=${assetId}&chat_mode=image`;
      const fetcher: typeof fetch = async (input, init = {}) => {
        const url = input instanceof URL ? input.href : String(input);
        const method = init.method ?? "GET";
        if (url === historyUrl) {
          if (scenario === "history-read") return jsonResponse({ error: "unauthorized" }, 401);
          return jsonResponse([currentMessage]);
        }
        if (url === `${apiBaseUrl}/upload/retry-oss` && method === "POST") {
          return jsonResponse({ error: "unauthorized" }, 401);
        }
        if (url === imageUrl) {
          return responseWithUrl(new Response("unauthorized", { status: 401 }), imageUrl);
        }
        throw new Error(`Unexpected request: ${method} ${url}`);
      };

      const { storage } = renderHistory(fetcher, { onUnauthorized });
      activeStorage = storage;
      fireEvent.change(await screen.findByLabelText("选择历史所属章节"), { target: { value: chapterId } });

      if (scenario === "history-read") {
        await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
        expect(screen.getByRole("alert").textContent).toContain("登录状态已失效");
      } else if (scenario === "transfer") {
        fireEvent.click(await screen.findByRole("button", { name: "转存到 OSS" }));
        await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
      } else {
        fireEvent.click(await screen.findByRole("button", { name: "预览图片" }));
        await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
      }

      expect(storage.getItem(API_TOKEN_KEY)).toBeNull();
      expect(storage.getItem(API_USER_KEY)).toBeNull();
      expect(onUnauthorized).toHaveBeenCalledTimes(1);
    },
  );

  it("does not clear or log out a replacement session when an older request returns 401", async () => {
    let resolveHistory: ((response: Response) => void) | undefined;
    const onUnauthorized = vi.fn();
    const historyUrl = `${apiBaseUrl}/chapters/${chapterId}/asset-chat-messages?asset_type=character&asset_id=${assetId}&chat_mode=image`;
    const fetcher: typeof fetch = async (input) => {
      const url = input instanceof URL ? input.href : String(input);
      if (url === historyUrl) {
        return await new Promise<Response>((resolve) => { resolveHistory = resolve; });
      }
      throw new Error(`Unexpected request: GET ${url}`);
    };
    const { storage } = renderHistory(fetcher, { onUnauthorized });
    fireEvent.change(await screen.findByLabelText("选择历史所属章节"), { target: { value: chapterId } });
    await waitFor(() => expect(resolveHistory).toBeTypeOf("function"));

    storage.setItem(API_TOKEN_KEY, "replacement-session-token");
    storage.setItem(API_USER_KEY, JSON.stringify({ id: "replacement-actor" }));
    resolveHistory?.(jsonResponse({ error: "unauthorized" }, 401));

    await waitFor(() => expect(storage.getItem(API_TOKEN_KEY)).toBe("replacement-session-token"));
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("loads only the requested preview on the visible page and revokes it when changing pages", async () => {
    const messages = Array.from({ length: 11 }, (_, index) => ({
      ...message(originalUrl),
      id: `history-message-page-${index + 1}`,
    }));
    const mediaSignals: AbortSignal[] = [];
    const mediaCalls: string[] = [];
    const fetcher: typeof fetch = async (input, init = {}) => {
      const url = input instanceof URL ? input.href : String(input);
      const method = init.method ?? "GET";
      if (url.includes("/asset-chat-messages?") && method === "GET") return jsonResponse(messages);
      if (url === originalUrl && method === "GET") {
        mediaCalls.push(url);
        if (init.signal instanceof AbortSignal) mediaSignals.push(init.signal);
        if (mediaCalls.length === 1) return pngResponse(url);
        return await new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
        });
      }
      throw new Error(`Unexpected request: ${method} ${url}`);
    };

    renderHistory(fetcher);
    fireEvent.change(await screen.findByLabelText("选择历史所属章节"), { target: { value: chapterId } });
    await screen.findByText("第 1 / 2 页");
    expect(screen.getAllByRole("button", { name: "预览图片" })).toHaveLength(10);
    expect(mediaCalls).toHaveLength(0);

    fireEvent.click(screen.getAllByRole("button", { name: "预览图片" })[0]!);
    await screen.findByRole("img", { name: "林照生成图片" });
    expect(mediaCalls).toHaveLength(1);
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "下一页历史" }));
    await screen.findByText("第 2 / 2 页");
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:history-preview"));
    expect(mediaCalls).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "预览图片" }));
    await waitFor(() => expect(mediaCalls).toHaveLength(2));
    fireEvent.click(screen.getByRole("button", { name: "上一页历史" }));
    await screen.findByText("第 1 / 2 页");
    await waitFor(() => expect(mediaSignals[1]?.aborted).toBe(true));
    expect(mediaCalls).toHaveLength(2);
  });
});
