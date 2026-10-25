import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../features/auth/AuthProvider";
import type { DirectoryHandleLike } from "../features/chapters/media-export/sinks";
import type { Chapter, Series, StoryboardFrame, User } from "../shared/api/contracts";
import type { PersonalRoughCutSnapshot } from "../shared/api/personalRoughCut";
import { createApiServices } from "../shared/api/services";
import { API_TOKEN_KEY } from "../shared/api/storage";
import { demoSeries } from "../features/series/demoSeries";
import { Workspace } from "./Workspace";

const apiBaseUrl = "http://127.0.0.1:4175/api";
const sessionToken = "media-export-session-token";
const authenticatedUser: User = {
  id: "media-export-user",
  username: "素材作者",
  email: "media-export@example.invalid",
  is_superuser: false,
  membership_type: "free",
  membership_expires_at: null,
  avatar_url: null,
  bio: null,
  created_at: "2026-10-01T00:00:00Z",
};
const mediaSeries: Series = {
  ...demoSeries[0]!,
  id: "media-export-series",
  user_id: authenticatedUser.id,
  name: "雨夜剧集",
  team_id: null,
  team_name: null,
  owner_name: authenticatedUser.username,
  claimed_by: null,
  claimed_by_username: null,
  claimed_by_avatar_url: null,
  can_enter: true,
};

const originalFetch = window.fetch;
const originalDirectoryPicker = Object.getOwnPropertyDescriptor(window, "showDirectoryPicker");
const originalCreateObjectUrl = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const originalRevokeObjectUrl = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
const pickerWindow = window as unknown as {
  showDirectoryPicker?: (options?: { readonly mode?: "readwrite" }) => Promise<DirectoryHandleLike>;
};

interface RequestRecord {
  readonly url: URL;
  readonly method: string;
  readonly headers: Headers;
  readonly body: unknown;
  readonly signal: AbortSignal | null;
  readonly credentials: RequestCredentials | undefined;
  readonly mode: RequestMode | undefined;
}

interface ScenarioOptions {
  readonly frames?: StoryboardFrame[];
  readonly roughCut?: PersonalRoughCutSnapshot;
  readonly fetchMedia?: (url: URL, signal: AbortSignal | null) => Promise<Response>;
}

interface MediaScenario {
  readonly requests: RequestRecord[];
  readonly fetcher: typeof fetch;
  readonly chapter: Chapter;
  readonly mount: () => void;
  readonly replaceServices: () => void;
}

function makeChapter(frames: StoryboardFrame[]): Chapter {
  return {
    id: "media-export-chapter",
    series_id: mediaSeries.id,
    title: "雨夜章节",
    content: frames,
    order: 0,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    lock: null,
  };
}

function responseWithUrl(body: string, url: string, contentType = "video/mp4", status = 200): Response {
  const response = new Response(body, { status, headers: { "Content-Type": contentType } });
  Object.defineProperty(response, "url", { value: url });
  return response;
}

function createScenario(options: ScenarioOptions): MediaScenario {
  const chapter = makeChapter(options.frames ?? []);
  const requests: RequestRecord[] = [];
  let rerender: ReturnType<typeof render>["rerender"] | null = null;
  const roughCut = options.roughCut ?? {
    chapter_id: chapter.id,
    revision: 9,
    saved: true,
    frames: [],
    removed_asset_ids: [],
  };
  const fetcher = vi.fn<typeof fetch>(async (input, init = {}) => {
    const url = new URL(String(input));
    const method = init.method ?? "GET";
    const headers = new Headers(init.headers);
    const body = typeof init.body === "string" ? JSON.parse(init.body) as unknown : null;
    const signal = init.signal instanceof AbortSignal ? init.signal : null;
    requests.push({ url, method, headers, body, signal, credentials: init.credentials, mode: init.mode });

    if (url.pathname === "/api/auth/me") return Response.json(authenticatedUser);
    if (url.pathname === "/api/series" && method === "GET") return Response.json([mediaSeries]);
    if (url.pathname === "/api/teams/my" && method === "GET") return Response.json([]);
    if (url.pathname === `/api/series/${mediaSeries.id}/chapters` && method === "GET") {
      return Response.json([chapter]);
    }
    if (url.pathname === `/api/chapters/${chapter.id}/rough-cut` && method === "GET") {
      return Response.json(roughCut);
    }
    if (url.pathname === "/api/sign-download-urls" && method === "POST") {
      const requestBody = body as { readonly items: readonly { readonly url: string; readonly filename: string }[] };
      return Response.json({ items: requestBody.items.map((item) => ({ ...item, signed: false })) });
    }
    if (url.pathname.startsWith("/uploads/") || url.origin !== new URL(apiBaseUrl).origin) {
      if (options.fetchMedia !== undefined) return options.fetchMedia(url, signal);
      return responseWithUrl(`bytes:${url.pathname}`, url.href);
    }
    if (method === "GET") return Response.json([]);
    return Response.json({ detail: `Unexpected test request: ${method} ${url.pathname}` }, { status: 404 });
  });

  return {
    requests,
    fetcher,
    chapter,
    mount() {
      window.localStorage.clear();
      window.localStorage.setItem(API_TOKEN_KEY, sessionToken);
      window.fetch = fetcher;
      const nextServices = createApiServices(apiBaseUrl, {
        storage: window.localStorage,
        fetcher,
      });
      const transport = nextServices.createMediaExportSession?.(() => true);
      expect(transport).toBeDefined();
      expect(Object.keys(transport ?? {})).not.toContain("token");
      const rendered = render(
        <AuthProvider services={nextServices}>
          <Workspace />
        </AuthProvider>,
      );
      rerender = rendered.rerender;
    },
    replaceServices() {
      if (rerender === null) throw new Error("The Workspace scenario has not been mounted.");
      const nextServices = createApiServices(apiBaseUrl, {
        storage: window.localStorage,
        fetcher,
      });
      rerender(
        <AuthProvider services={nextServices}>
          <Workspace />
        </AuthProvider>,
      );
    },
  };
}

interface WrittenFile {
  readonly directory: string;
  readonly name: string;
  blob: Blob | null;
  closed: boolean;
}

function installDirectoryPicker(options: {
  readonly failClose?: (name: string) => boolean;
  readonly inspectRunDirectory?: (name: string) => Promise<DirectoryHandleLike>;
} = {}) {
  const files: WrittenFile[] = [];
  const directories: string[] = [];
  const runDirectories = new Map<string, DirectoryHandleLike>();
  const parentDirectory: DirectoryHandleLike = {
    async getDirectoryHandle(name, requestOptions) {
      if (requestOptions?.create === true) {
        let directory = runDirectories.get(name);
        if (directory === undefined) {
          directories.push(name);
          directory = makeRunDirectory(name);
          runDirectories.set(name, directory);
        }
        return directory;
      }
      if (name.startsWith("media-export-") && options.inspectRunDirectory !== undefined) {
        return options.inspectRunDirectory(name);
      }
      const existing = runDirectories.get(name);
      if (existing !== undefined) return existing;
      throw new DOMException("Not found", "NotFoundError");
    },
    async getFileHandle() {
      throw new DOMException("Not found", "NotFoundError");
    },
  };

  function makeRunDirectory(directoryName: string): DirectoryHandleLike {
    const names = new Set<string>();
    return {
      async getDirectoryHandle() {
        throw new DOMException("Not found", "NotFoundError");
      },
      async getFileHandle(name, requestOptions) {
        if (requestOptions?.create !== true) {
          throw new DOMException("Not found", "NotFoundError");
        }
        if (names.has(name)) throw new DOMException("Already exists", "InvalidModificationError");
        names.add(name);
        const file: WrittenFile = { directory: directoryName, name, blob: null, closed: false };
        files.push(file);
        return {
          async createWritable() {
            return {
              async write(blob) { file.blob = blob; },
              async close() {
                if (options.failClose?.(name) === true) throw new Error("close result unknown");
                file.closed = true;
              },
            };
          },
        };
      },
    };
  }

  const picker = vi.fn(async () => parentDirectory);
  Object.defineProperty(pickerWindow, "showDirectoryPicker", { configurable: true, value: picker });
  return { picker, files, directories };
}

async function openChapterEntry(user: ReturnType<typeof userEvent.setup>, scenario: MediaScenario) {
  await screen.findByRole("heading", { name: "我的剧集" });
  await user.click(await screen.findByRole("button", { name: `只读查看章节：${mediaSeries.name}` }));
  await screen.findByRole("heading", { name: scenario.chapter.title });
  await user.click(screen.getByRole("button", { name: "导出本章视频" }));
  return screen.findByRole("dialog", { name: "导出媒体素材" });
}

async function confirmCurrentQueue(user: ReturnType<typeof userEvent.setup>, message: string) {
  await user.click(screen.getByRole("button", { name: "确认导出清单" }));
  await screen.findByText(/已确认 .* 个可导出视频/u);
  await user.click(screen.getByLabelText(message));
}

function restoreGlobals(): void {
  window.fetch = originalFetch;
  window.localStorage.clear();
  if (originalDirectoryPicker === undefined) Reflect.deleteProperty(pickerWindow, "showDirectoryPicker");
  else Object.defineProperty(pickerWindow, "showDirectoryPicker", originalDirectoryPicker);
  if (originalCreateObjectUrl === undefined) Reflect.deleteProperty(URL, "createObjectURL");
  else Object.defineProperty(URL, "createObjectURL", originalCreateObjectUrl);
  if (originalRevokeObjectUrl === undefined) Reflect.deleteProperty(URL, "revokeObjectURL");
  else Object.defineProperty(URL, "revokeObjectURL", originalRevokeObjectUrl);
}

afterEach(() => {
  cleanup();
  restoreGlobals();
  vi.restoreAllMocks();
});

describe("media export through authenticated Workspace entries", () => {
  it("freezes chapter selection and writes the selected public source bytes in chapter order", async () => {
    const user = userEvent.setup();
    const firstUrl = "https://cdn.example.invalid/scene-one.mp4";
    const thirdUrl = "https://cdn.example.invalid/scene-three.mp4";
    const scenario = createScenario({
      frames: [
        { text: "首镜", storyboard: ["asset-one"], preview: firstUrl },
        { text: "缺少视频", storyboard: ["asset-missing"] },
        { text: "第三镜", storyboard: ["asset-three"], preview: thirdUrl },
      ],
    });
    const output = installDirectoryPicker();
    scenario.mount();

    const dialog = await openChapterEntry(user, scenario);
    expect(within(dialog).getByText("导出来源：本章逐镜")).toBeInTheDocument();
    expect(within(dialog).getByText("缺少视频 1")).toBeInTheDocument();
    expect(within(dialog).getByText("已排除 0")).toBeInTheDocument();
    expect(within(dialog).getByRole("checkbox", { name: "选择分镜 1" })).toBeChecked();
    expect(within(dialog).getByRole("checkbox", { name: "选择分镜 3" })).toBeChecked();
    expect(dialog.contains(document.activeElement)).toBe(true);
    await user.click(within(dialog).getByRole("checkbox", { name: "选择分镜 3" }));
    expect(within(dialog).getByText("已排除 1")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("checkbox", { name: "选择分镜 3" }));

    await confirmCurrentQueue(user, "我已核对本次选择的分镜与导出清单。");
    const confirmedItems = within(dialog).getByRole("list", { name: "已确认的导出清单" });
    expect(within(confirmedItems).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      expect.stringContaining("1_雨夜章节_雨夜剧集.mp4"),
      expect.stringContaining("2_雨夜章节_雨夜剧集.mp4"),
      expect.stringContaining("3_雨夜章节_雨夜剧集.mp4"),
    ]);
    await user.click(within(dialog).getByRole("button", { name: "选择目录并开始" }));

    await waitFor(() => expect(output.files.filter((file) => file.closed)).toHaveLength(2));
    expect(output.picker).toHaveBeenCalledOnce();
    expect(await output.files[0]?.blob?.text()).toBe("bytes:/scene-one.mp4");
    expect(await output.files[1]?.blob?.text()).toBe("bytes:/scene-three.mp4");
    expect(output.files.map((file) => file.name)).toEqual([
      "1_雨夜章节_雨夜剧集.mp4",
      "3_雨夜章节_雨夜剧集.mp4",
    ]);

    const signer = scenario.requests.find((request) => request.url.pathname === "/api/sign-download-urls");
    expect(signer?.headers.get("Authorization")).toBe(`Bearer ${sessionToken}`);
    expect(signer?.body).toEqual({ items: [
      { url: firstUrl, filename: "1_雨夜章节_雨夜剧集.mp4" },
      { url: thirdUrl, filename: "3_雨夜章节_雨夜剧集.mp4" },
    ] });
    const publicReads = scenario.requests.filter((request) => request.url.href === firstUrl || request.url.href === thirdUrl);
    expect(publicReads).toHaveLength(2);
    expect(publicReads.every((request) => !request.headers.has("Authorization"))).toBe(true);
    expect(publicReads.every((request) => request.signal !== null)).toBe(true);
    expect(publicReads.every((request) => request.credentials === "omit" && request.mode === "cors")).toBe(true);
    expect(publicReads.every((request) => request.url.search === "")).toBe(true);
    expect(document.body.textContent).not.toContain(sessionToken);
  });

  it("uses the browser-download fallback when directory access is unavailable", async () => {
    const user = userEvent.setup();
    const sourceUrl = "https://cdn.example.invalid/browser-fallback.webm";
    const scenario = createScenario({
      frames: [{ text: "浏览器下载镜头", storyboard: ["asset-browser-fallback"], preview: sourceUrl }],
      fetchMedia: async (url) => responseWithUrl("browser bytes", url.href, "video/webm"),
    });
    Object.defineProperty(pickerWindow, "showDirectoryPicker", { configurable: true, value: undefined });
    const createObjectUrl = vi.fn(() => "blob:media-export-test");
    const revokeObjectUrl = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectUrl });
    const clickedNames: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clickedNames.push(this.download);
    });
    scenario.mount();

    const dialog = await openChapterEntry(user, scenario);
    await confirmCurrentQueue(user, "我已核对本次选择的分镜与导出清单。");
    expect(within(dialog).getByText(/浏览器下载只表示已发起/u)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "使用浏览器下载" }));

    expect(await within(dialog).findByText("已发起浏览器下载，磁盘结果未确认")).toBeInTheDocument();
    expect(createObjectUrl).toHaveBeenCalledWith(expect.any(Blob));
    expect(clickedNames).toEqual(["1_雨夜章节_雨夜剧集.webm"]);
    expect(revokeObjectUrl).not.toHaveBeenCalled();
  });

  it("exports the actual saved rough-cut order with protected and public media credential isolation", async () => {
    const user = userEvent.setup();
    const protectedUrl = "http://127.0.0.1:4175/uploads/saved-two.mp4";
    const publicUrl = "https://cdn.example.invalid/saved-one.mp4";
    const scenario = createScenario({
      frames: [
        { text: "章节第一镜", storyboard: ["asset-one"], preview: publicUrl },
        { text: "章节第二镜", storyboard: ["asset-two"], preview: protectedUrl },
      ],
      roughCut: {
        chapter_id: "media-export-chapter",
        revision: 12,
        saved: true,
        frames: [
          { asset_id: "asset-two", frame_index: 1, text: "草剪顺序第一项", preview_url: protectedUrl, missing_reason: null, included: true, pending: false },
          { asset_id: "asset-one", frame_index: 0, text: "草剪顺序第二项", preview_url: publicUrl, missing_reason: null, included: true, pending: false },
          { asset_id: "asset-pending", frame_index: 2, text: "等待项", preview_url: publicUrl, missing_reason: null, included: true, pending: true },
          { asset_id: "asset-excluded", frame_index: 3, text: "排除项", preview_url: publicUrl, missing_reason: null, included: false, pending: false },
          { asset_id: "asset-missing", frame_index: 4, text: "缺项", preview_url: null, missing_reason: "没有视频", included: true, pending: false },
        ],
        removed_asset_ids: [],
      },
    });
    const output = installDirectoryPicker();
    scenario.mount();

    await screen.findByRole("heading", { name: "我的剧集" });
    await user.click(await screen.findByRole("button", { name: `只读查看章节：${mediaSeries.name}` }));
    await screen.findByRole("heading", { name: scenario.chapter.title });
    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    const panel = await screen.findByRole("region", { name: "我的粗剪草稿" });
    await within(panel).findByText("草剪顺序第一项");
    await user.click(within(panel).getByRole("button", { name: "导出已保存粗剪" }));
    const dialog = await screen.findByRole("dialog", { name: "导出媒体素材" });
    expect(within(dialog).getByText("导出来源：已保存粗剪")).toBeInTheDocument();
    expect(within(dialog).getByText("仍在等待 1")).toBeInTheDocument();
    expect(within(dialog).getByText("已排除 1")).toBeInTheDocument();
    expect(within(dialog).getByText("缺少视频 1")).toBeInTheDocument();
    await confirmCurrentQueue(user, "我已核对当前已保存粗剪的导出清单与顺序。");
    await user.click(within(dialog).getByRole("button", { name: "选择目录并开始" }));

    await waitFor(() => expect(output.files.filter((file) => file.closed)).toHaveLength(2));
    expect(await output.files[0]?.blob?.text()).toBe("bytes:/uploads/saved-two.mp4");
    expect(await output.files[1]?.blob?.text()).toBe("bytes:/saved-one.mp4");
    expect(output.files.map((file) => file.name)).toEqual([
      expect.stringMatching(/^粗剪001_分镜002_雨夜章节_雨夜剧集\.mp4$/u),
      expect.stringMatching(/^粗剪002_分镜001_雨夜章节_雨夜剧集\.mp4$/u),
    ]);
    const signRequest = scenario.requests.find((request) => request.url.pathname === "/api/sign-download-urls");
    expect(signRequest?.headers.get("Authorization")).toBe(`Bearer ${sessionToken}`);
    expect(signRequest?.body).toEqual({ items: [
      { url: protectedUrl, filename: expect.stringMatching(/^粗剪001/u) },
      { url: publicUrl, filename: expect.stringMatching(/^粗剪002/u) },
    ] });
    const protectedRead = scenario.requests.find((request) => request.url.href === protectedUrl);
    const publicRead = scenario.requests.find((request) => request.url.href === publicUrl);
    expect(protectedRead?.headers.get("Authorization")).toBe(`Bearer ${sessionToken}`);
    expect(publicRead?.headers.has("Authorization")).toBe(false);
    expect(publicRead?.signal).not.toBeNull();
    expect(document.body.textContent).not.toContain(sessionToken);
  });

  it("retries only a known failed item after explicit confirmation and a newly selected target", async () => {
    const user = userEvent.setup();
    const sourceUrl = "https://cdn.example.invalid/retry.mp4";
    let reads = 0;
    const scenario = createScenario({
      frames: [{ text: "可重试镜头", storyboard: ["asset-retry"], preview: sourceUrl }],
      fetchMedia: async (url) => {
        reads += 1;
        return reads === 1
          ? responseWithUrl("temporary failure", url.href, "text/plain", 503)
          : responseWithUrl("recovered bytes", url.href);
      },
    });
    const output = installDirectoryPicker();
    scenario.mount();

    const dialog = await openChapterEntry(user, scenario);
    await confirmCurrentQueue(user, "我已核对本次选择的分镜与导出清单。");
    await user.click(within(dialog).getByRole("button", { name: "选择目录并开始" }));
    expect(await within(dialog).findByText("无法读取此视频素材。" )).toBeInTheDocument();
    expect(within(dialog).getByRole("checkbox", { name: /选择失败项/u })).toBeInTheDocument();
    expect(reads).toBe(1);

    await user.click(within(dialog).getByRole("checkbox", { name: /选择失败项/u }));
    await user.click(within(dialog).getByLabelText(/我确认只重试所选失败项/u));
    await user.click(within(dialog).getByRole("button", { name: "选择新目录并重试" }));
    await waitFor(() => expect(output.files.some((file) => file.closed && file.blob !== null)).toBe(true));
    expect(reads).toBe(2);
    expect(output.directories).toHaveLength(2);
    expect(output.directories[0]).not.toBe(output.directories[1]);
    expect(await output.files.at(-1)?.blob?.text()).toBe("recovered bytes");
  });

  it("keeps a failed close as unknown and never offers or performs an automatic replay", async () => {
    const user = userEvent.setup();
    const sourceUrl = "https://cdn.example.invalid/uncertain.mp4";
    let reads = 0;
    const scenario = createScenario({
      frames: [{ text: "结果未知镜头", storyboard: ["asset-unknown"], preview: sourceUrl }],
      fetchMedia: async (url) => {
        reads += 1;
        return responseWithUrl("written but close uncertain", url.href);
      },
    });
    const output = installDirectoryPicker({ failClose: () => true });
    scenario.mount();

    const dialog = await openChapterEntry(user, scenario);
    await confirmCurrentQueue(user, "我已核对本次选择的分镜与导出清单。");
    await user.click(within(dialog).getByRole("button", { name: "选择目录并开始" }));
    expect(await within(dialog).findByText(/文件结果未知/u)).toBeInTheDocument();
    expect(within(dialog).queryByRole("checkbox", { name: /选择失败项/u })).not.toBeInTheDocument();
    expect(reads).toBe(1);
    expect(output.files).toHaveLength(1);
    expect(output.files[0]?.closed).toBe(false);
    expect(scenario.requests.filter((request) => request.url.href === sourceUrl)).toHaveLength(1);
  });

  it.each([
    { label: "current protected API media", url: "http://127.0.0.1:4175/uploads/private.mp4", logsOut: true },
    { label: "anonymous public provider media", url: "https://cdn.example.invalid/private.mp4", logsOut: false },
  ])("distinguishes a 401 from $label", async ({ url, logsOut }) => {
    const user = userEvent.setup();
    const scenario = createScenario({
      frames: [{ text: "认证边界", storyboard: ["asset-auth"], preview: url }],
      fetchMedia: async (mediaUrl) => responseWithUrl("unauthorized", mediaUrl.href, "text/plain", 401),
    });
    const output = installDirectoryPicker();
    scenario.mount();
    const dialog = await openChapterEntry(user, scenario);
    await confirmCurrentQueue(user, "我已核对本次选择的分镜与导出清单。");
    await user.click(within(dialog).getByRole("button", { name: "选择目录并开始" }));

    if (logsOut) {
      expect(await screen.findByRole("heading", { name: "欢迎回来" })).toBeInTheDocument();
      expect(screen.queryByRole("dialog", { name: "导出媒体素材" })).not.toBeInTheDocument();
    } else {
      expect(await within(dialog).findByText("无法读取此视频素材。" )).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "欢迎回来" })).not.toBeInTheDocument();
    }
    const request = scenario.requests.find((candidate) => candidate.url.href === url);
    expect(request?.headers.has("Authorization")).toBe(logsOut);
    expect(output.files).toHaveLength(0);
  });

  it("closes the review dialog when its captured API session changes before export starts", async () => {
    const user = userEvent.setup();
    const scenario = createScenario({
      frames: [{
        text: "尚未开始导出的镜头",
        storyboard: ["asset-session-review"],
        preview: "https://cdn.example.invalid/session-review.mp4",
      }],
    });
    scenario.mount();
    await openChapterEntry(user, scenario);

    window.localStorage.setItem(API_TOKEN_KEY, "rotated-before-start-token");
    window.dispatchEvent(new StorageEvent("storage", {
      key: API_TOKEN_KEY,
      newValue: "rotated-before-start-token",
    }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "导出媒体素材" })).not.toBeInTheDocument());
    expect(scenario.requests.some((request) => request.url.pathname === "/api/sign-download-urls")).toBe(false);
  });

  it("rejects a stale review session on the next action without relying on a storage event", async () => {
    const user = userEvent.setup();
    const scenario = createScenario({
      frames: [{
        text: "同标签页凭据变化",
        storyboard: ["asset-same-tab-session"],
        preview: "https://cdn.example.invalid/same-tab-session.mp4",
      }],
    });
    const output = installDirectoryPicker();
    scenario.mount();
    const dialog = await openChapterEntry(user, scenario);
    await confirmCurrentQueue(user, "我已核对本次选择的分镜与导出清单。");

    window.localStorage.setItem(API_TOKEN_KEY, "same-tab-rotated-token");
    await user.click(within(dialog).getByRole("button", { name: "选择目录并开始" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "导出媒体素材" })).not.toBeInTheDocument());
    expect(output.picker).not.toHaveBeenCalled();
    expect(scenario.requests.some((request) => request.url.pathname === "/api/sign-download-urls")).toBe(false);
  });

  it("does not create a run directory when its pending existence check resolves after close", async () => {
    const user = userEvent.setup();
    let rejectInspection!: (error: unknown) => void;
    let markInspectionStarted!: () => void;
    const inspectionStarted = new Promise<void>((resolve) => { markInspectionStarted = resolve; });
    const inspection = new Promise<DirectoryHandleLike>((_resolve, reject) => { rejectInspection = reject; });
    const sourceUrl = "https://cdn.example.invalid/late-directory-check.mp4";
    const scenario = createScenario({
      frames: [{ text: "目录检查期间关闭", storyboard: ["asset-late-directory"], preview: sourceUrl }],
    });
    const output = installDirectoryPicker({
      inspectRunDirectory() {
        markInspectionStarted();
        return inspection;
      },
    });
    scenario.mount();
    const dialog = await openChapterEntry(user, scenario);
    await confirmCurrentQueue(user, "我已核对本次选择的分镜与导出清单。");
    await user.click(within(dialog).getByRole("button", { name: "选择目录并开始" }));
    await inspectionStarted;

    await user.click(within(dialog).getByRole("button", { name: "停止当前导出并关闭" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "导出媒体素材" })).not.toBeInTheDocument());
    await act(async () => {
      rejectInspection(new DOMException("Not found", "NotFoundError"));
      await new Promise<void>((resolve) => window.setTimeout(resolve, 0));
    });

    expect(output.directories).toHaveLength(0);
    expect(output.files).toHaveLength(0);
    expect(scenario.requests.some((request) => request.url.pathname === "/api/sign-download-urls")).toBe(false);
    expect(scenario.requests.some((request) => request.url.href === sourceUrl)).toBe(false);
    expect(screen.queryByRole("heading", { name: "欢迎回来" })).not.toBeInTheDocument();
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe(sessionToken);
  });

  it("aborts a late old-session 401 when the captured workspace identity changes", async () => {
    const user = userEvent.setup();
    let releaseMedia!: (response: Response) => void;
    const mediaSignal: { current: AbortSignal | null } = { current: null };
    const sourceUrl = "https://cdn.example.invalid/late.mp4";
    const scenario = createScenario({
      frames: [{ text: "迟到回执", storyboard: ["asset-late"], preview: sourceUrl }],
      fetchMedia: async (_url, signal) => {
        mediaSignal.current = signal;
        return new Promise<Response>((resolve) => { releaseMedia = resolve; });
      },
    });
    const output = installDirectoryPicker();
    scenario.mount();
    const dialog = await openChapterEntry(user, scenario);
    await confirmCurrentQueue(user, "我已核对本次选择的分镜与导出清单。");
    await user.click(within(dialog).getByRole("button", { name: "选择目录并开始" }));
    await waitFor(() => expect(mediaSignal.current).not.toBeNull());

    window.localStorage.setItem(API_TOKEN_KEY, "rotated-session-token");
    window.dispatchEvent(new StorageEvent("storage", { key: API_TOKEN_KEY, newValue: "rotated-session-token" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "导出媒体素材" })).not.toBeInTheDocument());
    expect(mediaSignal.current?.aborted).toBe(true);
    await act(async () => {
      releaseMedia(responseWithUrl("late unauthorized", sourceUrl, "text/plain", 401));
      await new Promise<void>((resolve) => window.setTimeout(resolve, 0));
    });
    expect(screen.queryByRole("heading", { name: "欢迎回来" })).not.toBeInTheDocument();
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("rotated-session-token");
    expect(output.files).toHaveLength(0);
  });

  it("aborts the active read when the Workspace API service identity changes", async () => {
    const user = userEvent.setup();
    let releaseMedia!: (response: Response) => void;
    const mediaSignal: { current: AbortSignal | null } = { current: null };
    const sourceUrl = "https://cdn.example.invalid/service-change.mp4";
    const scenario = createScenario({
      frames: [{ text: "服务变化时正在读取", storyboard: ["asset-service-change"], preview: sourceUrl }],
      fetchMedia: async (_url, signal) => {
        mediaSignal.current = signal;
        return new Promise<Response>((resolve) => { releaseMedia = resolve; });
      },
    });
    installDirectoryPicker();
    scenario.mount();
    const dialog = await openChapterEntry(user, scenario);
    await confirmCurrentQueue(user, "我已核对本次选择的分镜与导出清单。");
    await user.click(within(dialog).getByRole("button", { name: "选择目录并开始" }));
    await waitFor(() => expect(mediaSignal.current).not.toBeNull());

    scenario.replaceServices();
    await waitFor(() => expect(mediaSignal.current?.aborted).toBe(true));
    await waitFor(() => expect(scenario.requests.filter(
      (request) => request.url.pathname === "/api/auth/me",
    )).toHaveLength(2));
    await act(async () => {
      releaseMedia(responseWithUrl("late service-scope unauthorized", sourceUrl, "text/plain", 401));
      await new Promise<void>((resolve) => window.setTimeout(resolve, 0));
    });
    expect(screen.queryByRole("heading", { name: "欢迎回来" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "导出媒体素材" })).not.toBeInTheDocument();
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe(sessionToken);
  });

  it("immediately aborts the active item when the user closes the export dialog", async () => {
    const user = userEvent.setup();
    const mediaSignal: { current: AbortSignal | null } = { current: null };
    const sourceUrl = "https://cdn.example.invalid/close-active.mp4";
    const scenario = createScenario({
      frames: [{ text: "关闭时正在读取", storyboard: ["asset-close"], preview: sourceUrl }],
      fetchMedia: async (_url, signal) => {
        mediaSignal.current = signal;
        return new Promise<Response>(() => undefined);
      },
    });
    const output = installDirectoryPicker();
    scenario.mount();
    const dialog = await openChapterEntry(user, scenario);
    await confirmCurrentQueue(user, "我已核对本次选择的分镜与导出清单。");
    await user.click(within(dialog).getByRole("button", { name: "选择目录并开始" }));
    await waitFor(() => expect(mediaSignal.current).not.toBeNull());

    await user.click(within(dialog).getByRole("button", { name: "停止当前导出并关闭" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "导出媒体素材" })).not.toBeInTheDocument());
    expect(mediaSignal.current?.aborted).toBe(true);
    expect(output.files).toHaveLength(0);
  });

  it("stops after the current file and returns focus to the rough-cut entry on close", async () => {
    const user = userEvent.setup();
    let releaseFirst!: (response: Response) => void;
    let firstStarted!: () => void;
    const started = new Promise<void>((resolve) => { firstStarted = resolve; });
    const firstUrl = "https://cdn.example.invalid/stop-one.mp4";
    const secondUrl = "https://cdn.example.invalid/stop-two.mp4";
    const scenario = createScenario({
      frames: [
        { text: "第一镜", storyboard: ["asset-stop-one"], preview: firstUrl },
        { text: "第二镜", storyboard: ["asset-stop-two"], preview: secondUrl },
      ],
      roughCut: {
        chapter_id: "media-export-chapter",
        revision: 10,
        saved: true,
        frames: [
          { asset_id: "asset-stop-one", frame_index: 0, text: "第一镜", preview_url: firstUrl, missing_reason: null, included: true, pending: false },
          { asset_id: "asset-stop-two", frame_index: 1, text: "第二镜", preview_url: secondUrl, missing_reason: null, included: true, pending: false },
        ],
        removed_asset_ids: [],
      },
      fetchMedia: async (url) => {
        if (url.href === firstUrl) {
          firstStarted();
          return new Promise<Response>((resolve) => { releaseFirst = resolve; });
        }
        return responseWithUrl("second bytes", url.href);
      },
    });
    const output = installDirectoryPicker();
    scenario.mount();
    await screen.findByRole("heading", { name: "我的剧集" });
    await user.click(await screen.findByRole("button", { name: `只读查看章节：${mediaSeries.name}` }));
    await screen.findByRole("heading", { name: scenario.chapter.title });
    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    const panel = await screen.findByRole("region", { name: "我的粗剪草稿" });
    const opener = within(panel).getByRole("button", { name: "导出已保存粗剪" });
    await user.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "导出媒体素材" });
    await confirmCurrentQueue(user, "我已核对当前已保存粗剪的导出清单与顺序。");
    await user.click(within(dialog).getByRole("button", { name: "选择目录并开始" }));
    await started;
    await user.click(within(dialog).getByRole("button", { name: "停止剩余文件" }));
    await act(async () => {
      releaseFirst(responseWithUrl("first bytes", firstUrl));
      await Promise.resolve();
    });
    await within(dialog).findByText(/本次队列已结束/u);
    expect(scenario.requests.filter((request) => request.url.href === firstUrl)).toHaveLength(1);
    expect(scenario.requests.filter((request) => request.url.href === secondUrl)).toHaveLength(0);
    expect(output.files.filter((file) => file.closed)).toHaveLength(1);

    await user.click(within(dialog).getByRole("button", { name: "关闭媒体导出" }));
    await waitFor(() => expect(opener).toHaveFocus());
  });
});
