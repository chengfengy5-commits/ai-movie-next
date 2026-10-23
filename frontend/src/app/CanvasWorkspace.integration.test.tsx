import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChapterBrowser } from "../features/chapters/ChapterBrowser";
import { demoSeries } from "../features/series/demoSeries";
import type { CanvasDataPort } from "../shared/api/canvasData";
import { createCanvasDataHttpPort } from "../shared/api/canvasDataHttp";
import { createDemoServices, type WorkspaceServices } from "../shared/api/services";
import { API_TOKEN_KEY, API_USER_KEY, type StorageLike } from "../shared/api/storage";

function memoryStorage(): StorageLike {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}

afterEach(() => cleanup());

describe("selected chapter to canvas integration", () => {
  it("opens through ChapterBrowser, writes via the authenticated HTTP adapter, then restores on reopen", async () => {
    const storage = memoryStorage();
    const sourceServices = createDemoServices(storage);
    await sourceServices.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    storage.setItem(API_TOKEN_KEY, "canvas-http-fixture-token");
    storage.setItem(API_USER_KEY, JSON.stringify({ id: "demo-user" }));
    const state = {
      id: null as string | null,
      version: 1,
      document: { version: 1, viewport: { x: 0, y: 0, zoom: 1 }, nodes: [], edges: [], notes: [] } as Record<string, unknown>,
      updatedBy: null as string | null,
      updatedAt: null as string | null,
    };
    let releasePutResponseBody!: () => void;
    const putResponseBodyGate = new Promise<void>((resolve) => {
      releasePutResponseBody = resolve;
    });
    const putResponseBodyStarted = vi.fn();
    const requests: Array<{ url: URL; method: string; body: unknown; headers: Headers }> = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const url = new URL(String(input));
      const method = init?.method ?? "GET";
      const body = init?.body === undefined ? null : JSON.parse(String(init.body)) as unknown;
      requests.push({ url, method, body, headers: new Headers(init?.headers) });
      if (method === "GET") {
        return Response.json({
          id: state.id,
          chapter_id: decodeURIComponent(url.pathname.split("/").at(-2) ?? ""),
          version: state.version,
          document_json: state.document,
          updated_by: state.updatedBy,
          updated_at: state.updatedAt,
          save_capability: "guarded-v1",
        });
      }
      const request = body as {
        document_json: Record<string, unknown>;
        save_guard: { id: string | null; version: number };
      };
      state.id ??= "canvas-http-a";
      state.version = request.save_guard.id === null ? 1 : request.save_guard.version + 1;
      state.document = request.document_json;
      state.updatedBy = "demo-user";
      state.updatedAt = "2026-10-22T04:00:00Z";
      const response = Response.json({
        id: state.id,
        chapter_id: decodeURIComponent(url.pathname.split("/").at(-2) ?? ""),
        version: state.version,
        document_json: state.document,
        updated_by: state.updatedBy,
        updated_at: state.updatedAt,
        save_receipt: { mode: "guarded-v1", id: state.id, version: state.version },
      });
      const readResponseJson = response.json.bind(response);
      response.json = async () => {
        putResponseBodyStarted();
        await putResponseBodyGate;
        return readResponseJson();
      };
      return response;
    });
    const port: CanvasDataPort = createCanvasDataHttpPort("http://127.0.0.1:8000/api", { storage, fetcher });
    const services = { ...sourceServices, canvasData: port } as WorkspaceServices;
    const onUnauthorized = vi.fn();
    const series = demoSeries[0]!;
    try {
      render(
        <ChapterBrowser
          onBack={vi.fn()}
          onUnauthorized={onUnauthorized}
          sessionGeneration={2}
          series={series}
          services={services}
          userId="demo-user"
        />,
      );

      const openButton = await screen.findByRole("button", { name: "创作画布" });
      fireEvent.click(openButton);
      const firstRegion = await screen.findByRole("region", { name: "画布工作区" });
      const firstDialog = firstRegion.closest('[role="dialog"]');
      if (!(firstDialog instanceof HTMLElement)) throw new Error("Canvas dialog was not rendered.");
      const canvas = within(firstDialog);
      const firstGet = requests.find((request) => request.method === "GET"
        && request.url.pathname.startsWith("/api/chapters/") && request.url.pathname.endsWith("/canvas"));
      if (firstGet === undefined) throw new Error("Canvas GET was not sent.");
      const canvasGets = () => requests.filter((request) => request.method === "GET"
        && request.url.pathname === firstGet.url.pathname);
      expect(canvasGets()).toHaveLength(1);
      expect(firstGet.url.searchParams.get("save_mode")).toBe("guarded-v1");
      expect(firstGet.headers.get("Authorization")).toBe("Bearer canvas-http-fixture-token");

      await canvas.findByText("已保存");
      const addCardButton = canvas.getByRole("button", { name: "新增文本卡片" });
      expect(addCardButton).not.toBeDisabled();
      fireEvent.click(addCardButton);
      fireEvent.change(await canvas.findByLabelText("文本卡片文字"), { target: { value: "保存后的章节构图说明" } });
      fireEvent.click(canvas.getByRole("button", { name: "保存" }));
      await waitFor(() => expect(putResponseBodyStarted).toHaveBeenCalledOnce());
      const putRequest = requests.find((request) => request.method === "PUT");
      expect(putRequest?.url.pathname).toBe(firstGet.url.pathname);
      expect(putRequest?.headers.get("Authorization")).toBe("Bearer canvas-http-fixture-token");
      expect(putRequest?.body).toMatchObject({
        version: 1,
        save_guard: { mode: "guarded-v1", id: null, version: 1 },
        document_json: { cards: [expect.objectContaining({ text: "保存后的章节构图说明" })] },
      });
      expect(canvas.getByText("正在保存…")).toBeTruthy();
      expect(canvas.queryByText("已保存")).toBeNull();

      // A request reaching the fixture is not a validated save receipt.
      await act(async () => { releasePutResponseBody(); });
      await canvas.findByText("已保存");
      fireEvent.click(canvas.getByRole("button", { name: "关闭创作画布" }));
      await waitFor(() => expect(firstDialog.isConnected).toBe(false));

      const reopenButton = await screen.findByRole("button", { name: "创作画布" });
      expect(reopenButton.closest("[inert]")).toBeNull();
      fireEvent.click(reopenButton);
      const reopenedRegion = await screen.findByRole("region", { name: "画布工作区" });
      expect(reopenedRegion).not.toBe(firstRegion);
      const reopenedDialog = reopenedRegion.closest('[role="dialog"]');
      if (!(reopenedDialog instanceof HTMLElement)) throw new Error("Reopened canvas dialog was not rendered.");
      expect(await within(reopenedDialog).findByDisplayValue("保存后的章节构图说明")).toBeTruthy();
      await waitFor(() => expect(canvasGets()).toHaveLength(2));
      expect(requests.filter((request) => request.method === "PUT")).toHaveLength(1);
      expect(state.version).toBe(1);
      expect(onUnauthorized).not.toHaveBeenCalled();
    } finally {
      releasePutResponseBody();
      cleanup();
    }
  });
});
