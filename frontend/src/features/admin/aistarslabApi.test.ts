import { describe, expect, it, vi } from "vitest";
import { createAistarslabApi, type AistarslabApiError } from "./aistarslabApi";
import type { AdminFetcher } from "./adminApi";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const configResponse = {
  imageConfig: [{
    channel: "image-line",
    title: "图片线路",
    defaultOption: true,
    models: [{ model: "image-model", label: "图片模型", private_value: "must-not-escape" }],
    api_key: "must-not-escape",
  }],
  videoConfig: [{
    channel: "video-line",
    title: "视频线路",
    defaultOption: true,
    models: [{ model: "video-model", label: "视频模型" }],
  }],
  credits: null,
  private_value: "must-not-escape",
};

describe("AIStarsLab API client", () => {
  it("does not dispatch a request when its caller signal is already aborted", async () => {
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse(configResponse));
    const api = createAistarslabApi(fetcher, () => "admin-token");
    const controller = new AbortController();
    controller.abort();

    await expect(api.getConfig(controller.signal)).rejects.toMatchObject({ kind: "aborted" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("uses the five existing routes, current admin bearer token and exact request bodies", async () => {
    const fetcher = vi.fn<AdminFetcher>(async (input) => {
      const url = String(input);
      if (url === "/api/aistarslab/config") return jsonResponse(configResponse);
      if (url === "/api/aistarslab/build-image-params") {
        return jsonResponse({ channel: "image-line", model: "image-model", label: "图片模型", lineTitle: "图片线路", params: "{}", api_key: "secret" });
      }
      if (url === "/api/aistarslab/build-params") {
        return jsonResponse({ channel: "video-line", model: "video-model", label: "视频模型", lineTitle: "视频线路", params: "{}" });
      }
      if (url === "/api/aistarslab/add-image-default") {
        return jsonResponse({ id: "image-default-1", user_id: "system", is_system: true, category: "image", provider: "aistarslab", name: "图片默认", model: "image-line:image-model", api_key: "encrypted-response" });
      }
      if (url === "/api/aistarslab/add-default") {
        return jsonResponse({ id: "video-default-1", user_id: "system", is_system: true, category: "video", provider: "aistarslab", name: "视频默认", model: "video-line:video-model", api_key: "encrypted-response" });
      }
      throw new Error("Unexpected request: " + url);
    });
    const api = createAistarslabApi(fetcher, () => "admin-token");

    const config = await api.getConfig();
    const imageParams = await api.buildParams("image", "image-line", "image-model");
    const videoParams = await api.buildParams("video", "video-line", "video-model");
    const imageModel = await api.addDefault("image", "image-draft-key");
    const videoModel = await api.addDefault("video", "video-draft-key");

    expect(config.imageConfig[0]).toEqual({
      channel: "image-line",
      title: "图片线路",
      isDefault: true,
      models: [{ model: "image-model", label: "图片模型" }],
    });
    expect(imageParams).toEqual({ channel: "image-line", model: "image-model", label: "图片模型", lineTitle: "图片线路", params: "{}" });
    expect(videoParams).toEqual({ channel: "video-line", model: "video-model", label: "视频模型", lineTitle: "视频线路", params: "{}" });
    expect(imageModel).toEqual({ id: "image-default-1", category: "image", name: "图片默认", model: "image-line:image-model" });
    expect(videoModel).toEqual({ id: "video-default-1", category: "video", name: "视频默认", model: "video-line:video-model" });
    expect(JSON.stringify([config, imageParams, videoParams, imageModel, videoModel])).not.toContain("must-not-escape");
    expect(JSON.stringify([config, imageParams, videoParams, imageModel, videoModel])).not.toContain("encrypted-response");

    expect(fetcher).toHaveBeenCalledTimes(5);
    expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/aistarslab/config",
      "/api/aistarslab/build-image-params",
      "/api/aistarslab/build-params",
      "/api/aistarslab/add-image-default",
      "/api/aistarslab/add-default",
    ]);
    for (const [url, init] of fetcher.mock.calls) {
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer admin-token");
      if (String(url).endsWith("/config")) {
        expect(init?.method).toBe("GET");
        expect(init?.body).toBeUndefined();
      }
    }
    expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body))).toEqual({ channel: "image-line", model: "image-model" });
    expect(JSON.parse(String(fetcher.mock.calls[2]?.[1]?.body))).toEqual({ channel: "video-line", model: "video-model" });
    expect(JSON.parse(String(fetcher.mock.calls[3]?.[1]?.body))).toEqual({ api_key: "image-draft-key" });
    expect(JSON.parse(String(fetcher.mock.calls[4]?.[1]?.body))).toEqual({ api_key: "video-draft-key" });
  });

  it("rejects wrong-category or malformed build and create success envelopes", async () => {
    const fetcher = vi.fn<AdminFetcher>(async (input) => {
      if (String(input).endsWith("/build-image-params")) {
        return jsonResponse({ channel: "wrong-line", model: "image-model", label: "图片模型", params: "{}" });
      }
      return jsonResponse({ id: "m1", user_id: "admin-1", is_system: false, category: "video", provider: "aistarslab", name: "模型", model: "line:model" });
    });
    const api = createAistarslabApi(fetcher, () => "admin-token");

    await expect(api.buildParams("image", "image-line", "image-model")).rejects.toMatchObject({ kind: "invalid-response" });
    await expect(api.addDefault("video", "key")).rejects.toMatchObject({ kind: "invalid-response" });
  });

  it("treats a non-200 success response as invalid instead of accepting it", async () => {
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse(configResponse, 202));
    const api = createAistarslabApi(fetcher, () => "admin-token");

    const error = await api.getConfig().catch((cause: AistarslabApiError) => cause);
    expect(error).toMatchObject({ kind: "invalid-response", status: 202 });
  });

  it("keeps caller abort and the request deadline active while parsing a 200 response body", async () => {
    vi.useFakeTimers();
    try {
      for (const stopMode of ["caller", "deadline"] as const) {
        let resolveJson: (value: unknown) => void = () => undefined;
        let requestSignal: AbortSignal | undefined;
        let jsonStarted = false;
        const body = new Promise<unknown>((resolve) => {
          resolveJson = resolve;
        });
        const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
          requestSignal = init?.signal ?? undefined;
          return {
            status: 200,
            ok: true,
            json: () => {
              jsonStarted = true;
              return body;
            },
          } as Response;
        });
        const controller = new AbortController();
        const api = createAistarslabApi(fetcher, () => "admin-token");
        const pending = api.getConfig(controller.signal);

        await waitForResponseBody(() => jsonStarted);
        expect(requestSignal).toBeDefined();
        const rejected = expect(pending).rejects.toMatchObject({
          kind: stopMode === "caller" ? "aborted" : "timeout",
        });
        if (stopMode === "caller") {
          controller.abort();
          expect(requestSignal?.aborted).toBe(true);
        } else {
          await vi.advanceTimersByTimeAsync(20_000);
          expect(requestSignal?.aborted).toBe(true);
        }
        await rejected;
        resolveJson(configResponse);
      }
    } finally {
      vi.useRealTimers();
    }
  });
});

async function waitForResponseBody(isStarted: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 10 && !isStarted(); attempt += 1) {
    await Promise.resolve();
  }
  expect(isStarted()).toBe(true);
}
