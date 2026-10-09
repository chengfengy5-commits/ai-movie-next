import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { demoSeries } from "../features/series/demoSeries";
import { AuthProvider } from "../features/auth/AuthProvider";
import { ApiError } from "../shared/api/errors";
import type { Chapter, StoryboardAsset, User } from "../shared/api/contracts";
import type {
  PersonalRoughCutSnapshot,
  PersonalRoughCutUpdate,
} from "../shared/api/personalRoughCut";
import { createDemoServices, type WorkspaceServices } from "../shared/api/services";
import {
  API_TOKEN_KEY,
  API_USER_KEY,
  MOCK_SESSION_KEY,
  MOCK_USER_KEY,
} from "../shared/api/storage";
import { Workspace } from "./Workspace";

const demoUser: User = {
  id: "demo-user",
  username: "演示创作者",
  email: "demo@example.invalid",
  is_superuser: false,
  membership_type: "demo",
  membership_expires_at: null,
  avatar_url: null,
  bio: null,
  created_at: "2026-10-01T00:00:00Z",
};
const testSeries = demoSeries[0]!;
const chapterId = "rough-cut-edit-integration-chapter";
const frameIds = ["rough-cut-edit-frame-a", "rough-cut-edit-frame-b"] as const;
const integrationChapter: Chapter = {
  id: chapterId,
  series_id: testSeries.id,
  title: "粗剪保存集成章节",
  content: frameIds.map((id, index) => ({
    text: "源镜头 " + (index + 1),
    original_text: "源镜头原文 " + (index + 1),
    storyboard: [id],
    preview: null,
  })),
  order: 2,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
  lock: null,
};
const integrationAssets: StoryboardAsset[] = frameIds.map((id, index) => ({
  id,
  series_id: testSeries.id,
  chapter_id: chapterId,
  frame_index: index,
  name: "合成镜头素材 " + (index + 1),
  description: null,
  image_url: null,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
}));

function snapshotFor(
  chapter: Chapter,
  text: string,
  revision = 1,
): PersonalRoughCutSnapshot {
  return {
    chapter_id: chapter.id,
    revision,
    saved: revision > 0,
    frames: frameIds.map((id, frame_index) => ({
      asset_id: id,
      frame_index,
      text: text + " · " + (frame_index + 1),
      preview_url: null,
      missing_reason: null,
      included: true,
      pending: false,
    })),
    removed_asset_ids: [],
  };
}

function cloneSnapshot(snapshot: PersonalRoughCutSnapshot): PersonalRoughCutSnapshot {
  return {
    ...snapshot,
    frames: snapshot.frames.map((frame) => ({ ...frame })),
    removed_asset_ids: [...snapshot.removed_asset_ids],
  };
}

function applyUpdate(
  current: PersonalRoughCutSnapshot,
  update: PersonalRoughCutUpdate,
): PersonalRoughCutSnapshot {
  const framesById = new Map(current.frames.map((frame) => [frame.asset_id, frame]));
  return {
    chapter_id: current.chapter_id,
    revision: current.revision + 1,
    saved: true,
    frames: update.frames.map((entry) => {
      const source = framesById.get(entry.asset_id);
      if (source === undefined) {
        throw new Error("The rough-cut update did not match the source set.");
      }
      return { ...source, included: entry.included, pending: false };
    }),
    removed_asset_ids: [],
  };
}

function renderWorkspace(services: WorkspaceServices) {
  return render(
    <AuthProvider services={services}>
      <Workspace />
    </AuthProvider>,
  );
}

function installScrollProbe(): { targets: HTMLElement[]; restore(): void } {
  const previous = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");
  const targets: HTMLElement[] = [];
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value(this: HTMLElement) {
      targets.push(this);
    },
  });
  return {
    targets,
    restore() {
      if (previous === undefined) {
        Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
      } else {
        Object.defineProperty(HTMLElement.prototype, "scrollIntoView", previous);
      }
    },
  };
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

function authenticatedUser(id: string, username: string): User {
  return {
    ...demoUser,
    id,
    username,
    membership_type: "free",
  };
}

async function openSeries(user: ReturnType<typeof userEvent.setup>) {
  const list = await screen.findByRole("region", { name: "我的剧集" });
  const heading = await within(list).findByRole("heading", { name: testSeries.name });
  const card = heading.closest("article");
  if (!(card instanceof HTMLElement)) {
    throw new Error("The demo series card was not rendered.");
  }
  await user.click(within(card).getByRole("button", { name: /只读查看章节/ }));
  return screen.findByRole("region", { name: testSeries.name });
}

async function openIntegrationChapter(user: ReturnType<typeof userEvent.setup>) {
  const chapterRegion = await openSeries(user);
  await within(chapterRegion).findByRole("button", { name: /粗剪保存集成章节/ });
  return chapterRegion;
}

function seedDemoSession(): void {
  window.localStorage.setItem(MOCK_SESSION_KEY, "demo-session");
  window.localStorage.setItem(MOCK_USER_KEY, JSON.stringify(demoUser));
}

function seedApiSession(user: User, token: string): void {
  window.localStorage.setItem(API_TOKEN_KEY, token);
  window.localStorage.setItem(API_USER_KEY, JSON.stringify(user));
}

function apiServices(options: {
  firstUser: User;
  secondUser: User;
  getRoughCut: WorkspaceServices["getPersonalRoughCut"];
  saveRoughCut: NonNullable<WorkspaceServices["savePersonalRoughCut"]>;
  logout?: () => void;
}): WorkspaceServices {
  return {
    mode: "api",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    restore: async () => options.firstUser,
    login: async ({ username }) => {
      if (username !== "second") {
        throw new ApiError("http", "账号或密码不正确。", 401);
      }
      window.localStorage.setItem(API_TOKEN_KEY, "rough-cut-edit-second-token");
      window.localStorage.setItem(API_USER_KEY, JSON.stringify(options.secondUser));
      return options.secondUser;
    },
    listSeries: async () => demoSeries,
    listMyTeams: async () => [],
    listChapters: async () => [integrationChapter],
    listStoryboardAssets: async () => integrationAssets.map((asset) => ({ ...asset })),
    listCharacters: async () => [],
    listScenes: async () => [],
    listProps: async () => [],
    getPersonalProductionNotes: async () => {
      throw new Error("Unexpected personal-notes request.");
    },
    getPersonalRoughCut: (requestedChapterId, signal) => {
      if (requestedChapterId !== chapterId) {
        throw new Error("Unexpected rough-cut chapter ID.");
      }
      return options.getRoughCut(requestedChapterId, signal);
    },
    savePersonalRoughCut: (requestedChapterId, update, signal) => {
      if (requestedChapterId !== chapterId) {
        throw new Error("Unexpected rough-cut chapter ID.");
      }
      return options.saveRoughCut(requestedChapterId, update, signal);
    },
    listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    logout: () => {
      if (options.logout !== undefined) {
        options.logout();
      } else {
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      }
    },
  };
}

async function signInSecondAccount(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "退出登录" }));
  const login = await screen.findByRole("region", { name: "欢迎回来" });
  await user.click(within(login).getByLabelText("账号"));
  await user.paste("second");
  await user.click(within(login).getByLabelText("密码"));
  await user.paste("password");
  await user.click(within(login).getByRole("button", { name: "登录工作台" }));
}

let scrollProbe: ReturnType<typeof installScrollProbe> | null = null;

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  scrollProbe?.restore();
  scrollProbe = null;
  window.localStorage.clear();
});

describe("personal rough-cut editing through Workspace", () => {
  it("creates a revision-zero demo draft, saves it without a hidden reread, and reopens the saved source projection", async () => {
    seedDemoSession();
    const base = createDemoServices(window.localStorage);
    const getRoughCut = vi.fn(base.getPersonalRoughCut.bind(base));
    const saveMethod = base.savePersonalRoughCut;
    if (saveMethod === undefined) {
      throw new Error("Demo service must provide rough-cut save capability.");
    }
    const save = vi.fn(saveMethod.bind(base));
    const listChapters = vi.fn(base.listChapters.bind(base));
    const listAssets = vi.fn(base.listStoryboardAssets.bind(base));
    const services: WorkspaceServices = {
      ...base,
      getPersonalRoughCut: getRoughCut,
      savePersonalRoughCut: save,
      listChapters,
      listStoryboardAssets: listAssets,
    };
    const user = userEvent.setup();
    scrollProbe = installScrollProbe();
    renderWorkspace(services);

    const chapterRegion = await openSeries(user);
    const secondChapter = await within(chapterRegion).findByRole("button", { name: /第二章 · 来信/ });
    await user.click(secondChapter);
    const selectedChapter = await within(chapterRegion).findByRole("heading", { name: "第二章 · 来信" });
    expect(selectedChapter).toBeInTheDocument();
    await user.click(within(chapterRegion).getByRole("button", { name: "查看我的粗剪草稿" }));
    const panel = await within(chapterRegion).findByRole("region", { name: "我的粗剪草稿" });
    expect(await within(panel).findByText("未保存的初始投影")).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(1);
    expect(getRoughCut.mock.results[0]?.value).resolves.toMatchObject({
      revision: 0,
      saved: false,
      frames: [{ included: true, pending: false }],
    });

    await waitFor(() => expect(within(panel).getByRole("button", { name: "编辑编排" })).toBeEnabled());
    await user.click(within(panel).getByRole("button", { name: "编辑编排" }));
    const editor = within(panel).getByRole("region", { name: "编辑粗剪编排" });
    const exclude = within(editor).getByRole("button", { name: "排除粗剪：镜头 1" });
    await user.click(exclude);
    expect(within(editor).getByRole("button", { name: "纳入粗剪：镜头 1" })).toHaveAttribute("aria-pressed", "false");
    await user.click(within(editor).getByRole("button", { name: "保存粗剪编排" }));

    expect(await within(panel).findByText("已保存草稿的当前投影 · 版本 1")).toBeInTheDocument();
    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0]?.[0]).toBe("demo-series-01-chapter-letter");
    expect(save.mock.calls[0]?.[1]).toEqual({
      expected_revision: 0,
      frames: [{ asset_id: "demo-series-01-frame-letter-second", included: false }],
    });
    expect(getRoughCut).toHaveBeenCalledTimes(1);
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listAssets).toHaveBeenCalledTimes(2);

    await user.click(within(panel).getByRole("button", { name: "关闭" }));
    await user.click(within(chapterRegion).getByRole("button", { name: "查看我的粗剪草稿" }));
    const reopenedPanel = await within(chapterRegion).findByRole("region", { name: "我的粗剪草稿" });
    expect(await within(reopenedPanel).findByText("已保存草稿的当前投影 · 版本 1")).toBeInTheDocument();
    expect(within(reopenedPanel).getByText("已排除")).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(2);
    expect(save).toHaveBeenCalledTimes(1);

    const target = within(chapterRegion).getByRole("article", { name: "第二章 · 来信 · 镜头 1" });
    await user.click(within(reopenedPanel).getByRole("button", { name: "定位到对应镜头 1" }));
    await waitFor(() => expect(target).toHaveFocus());
    expect(scrollProbe?.targets).toContain(target);
    expect(within(chapterRegion).getByText("已定位到粗剪镜头 1。")).toBeInTheDocument();
    expect(within(chapterRegion).getByRole("region", { name: "我的粗剪草稿" })).toBeInTheDocument();
    expect(window.localStorage.getItem(MOCK_SESSION_KEY)).toBe("demo-session");
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBeNull();
  });

  it.each(["success", "401"] as const)(
    "settles a late ignored-abort rough-cut GET %s after logout without replacing the next account",
    async (lateResult) => {
      const firstUser = authenticatedUser("rough-cut-first-user", "旧粗剪账号");
      const secondUser = authenticatedUser("rough-cut-second-user", "新粗剪账号");
      const oldRead = deferred<PersonalRoughCutSnapshot>();
      const firstSnapshot = snapshotFor(integrationChapter, "旧用户私人粗剪");
      const secondSnapshot = snapshotFor(integrationChapter, "新用户私人粗剪", 3);
      let activeUserId = firstUser.id;
      const getRoughCut = vi.fn(async (requestedChapterId: string, _signal: AbortSignal) => {
        if (requestedChapterId !== chapterId) {
          throw new Error("Unexpected rough-cut chapter ID.");
        }
        if (activeUserId === firstUser.id) {
          return oldRead.promise;
        }
        return cloneSnapshot(secondSnapshot);
      });
      const saveRoughCut = vi.fn(async () => { throw new Error("Unexpected rough-cut save."); });
      const services = apiServices({ firstUser, secondUser, getRoughCut, saveRoughCut });
      const login = services.login;
      services.login = async (credentials, signal) => {
        const loggedIn = await login(credentials, signal);
        activeUserId = secondUser.id;
        return loggedIn;
      };
      const logout = services.logout;
      services.logout = () => {
        activeUserId = firstUser.id;
        logout();
      };
      seedApiSession(firstUser, "rough-cut-edit-first-token");
      const user = userEvent.setup();
      renderWorkspace(services);

      await screen.findByText(firstUser.username);
      const firstChapterRegion = await openIntegrationChapter(user);
      await user.click(within(firstChapterRegion).getByRole("button", { name: "查看我的粗剪草稿" }));
      expect(await within(firstChapterRegion).findByText("正在读取粗剪草稿…")).toBeInTheDocument();
      await waitFor(() => expect(getRoughCut).toHaveBeenCalledTimes(1));

      await signInSecondAccount(user);
      expect(await screen.findByText(secondUser.username)).toBeInTheDocument();
      const secondChapterRegion = await openIntegrationChapter(user);
      await user.click(within(secondChapterRegion).getByRole("button", { name: "查看我的粗剪草稿" }));
      const secondPanel = await within(secondChapterRegion).findByRole("region", { name: "我的粗剪草稿" });
      expect(await within(secondPanel).findByText("新用户私人粗剪 · 1")).toBeInTheDocument();
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("rough-cut-edit-second-token");

      await act(async () => {
        if (lateResult === "success") {
          oldRead.resolve(firstSnapshot);
          await oldRead.promise;
        } else {
          oldRead.reject(new ApiError("http", "登录状态已失效。", 401));
          await oldRead.promise.catch(() => undefined);
        }
      });

      expect(within(secondPanel).getByText("新用户私人粗剪 · 1")).toBeInTheDocument();
      expect(within(secondPanel).queryByText("旧用户私人粗剪 · 1")).not.toBeInTheDocument();
      expect(screen.getByText(secondUser.username)).toBeInTheDocument();
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("rough-cut-edit-second-token");
      expect(getRoughCut).toHaveBeenCalledTimes(2);
    },
  );

  it.each(["success", "401"] as const)(
    "settles a late ignored-abort rough-cut PUT %s without damaging the next account or its editor",
    async (lateResult) => {
      const firstUser = authenticatedUser("rough-cut-write-first-user", "旧粗剪保存账号");
      const secondUser = authenticatedUser("rough-cut-write-second-user", "新粗剪保存账号");
      const oldWrite = deferred<PersonalRoughCutSnapshot>();
      const firstSnapshot = snapshotFor(integrationChapter, "旧账号粗剪", 2);
      const secondSnapshot = snapshotFor(integrationChapter, "新账号粗剪", 4);
      let activeUserId = firstUser.id;
      const currentByUser = new Map([
        [firstUser.id, firstSnapshot],
        [secondUser.id, secondSnapshot],
      ]);
      const getRoughCut = vi.fn(async (requestedChapterId: string) => {
        if (requestedChapterId !== chapterId) {
          throw new Error("Unexpected rough-cut chapter ID.");
        }
        return cloneSnapshot(currentByUser.get(activeUserId)!);
      });
      const saveRoughCut = vi.fn((
        requestedChapterId: string,
        update: PersonalRoughCutUpdate,
        _signal: AbortSignal,
      ) => {
        if (requestedChapterId !== chapterId) {
          throw new Error("Unexpected rough-cut chapter ID.");
        }
        if (activeUserId === firstUser.id) {
          return oldWrite.promise;
        }
        const saved = applyUpdate(currentByUser.get(activeUserId)!, update);
        currentByUser.set(activeUserId, saved);
        return Promise.resolve(cloneSnapshot(saved));
      });
      const services = apiServices({ firstUser, secondUser, getRoughCut, saveRoughCut });
      const login = services.login;
      services.login = async (credentials, signal) => {
        const loggedIn = await login(credentials, signal);
        activeUserId = secondUser.id;
        return loggedIn;
      };
      const logout = services.logout;
      services.logout = () => {
        activeUserId = firstUser.id;
        logout();
      };
      seedApiSession(firstUser, "rough-cut-edit-write-first-token");
      const user = userEvent.setup();
      renderWorkspace(services);

      await screen.findByText(firstUser.username);
      const firstChapterRegion = await openIntegrationChapter(user);
      await user.click(within(firstChapterRegion).getByRole("button", { name: "查看我的粗剪草稿" }));
      const firstPanel = await within(firstChapterRegion).findByRole("region", { name: "我的粗剪草稿" });
      expect(await within(firstPanel).findByText("旧账号粗剪 · 1")).toBeInTheDocument();
      await waitFor(() => expect(within(firstPanel).getByRole("button", { name: "编辑编排" })).toBeEnabled());
      await user.click(within(firstPanel).getByRole("button", { name: "编辑编排" }));
      const editor = within(firstPanel).getByRole("region", { name: "编辑粗剪编排" });
      await user.click(within(editor).getByRole("button", { name: "排除粗剪：镜头 1" }));
      await user.click(within(editor).getByRole("button", { name: "保存粗剪编排" }));
      await waitFor(() => expect(saveRoughCut).toHaveBeenCalledTimes(1));

      await signInSecondAccount(user);
      expect(await screen.findByText(secondUser.username)).toBeInTheDocument();
      const secondChapterRegion = await openIntegrationChapter(user);
      await user.click(within(secondChapterRegion).getByRole("button", { name: "查看我的粗剪草稿" }));
      const secondPanel = await within(secondChapterRegion).findByRole("region", { name: "我的粗剪草稿" });
      expect(await within(secondPanel).findByText("新账号粗剪 · 1")).toBeInTheDocument();
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("rough-cut-edit-second-token");

      await act(async () => {
        if (lateResult === "success") {
          const update = saveRoughCut.mock.calls[0]?.[1];
          if (update === undefined) {
            throw new Error("The first account save did not retain its submitted update.");
          }
          const oldSaved = applyUpdate(firstSnapshot, update);
          currentByUser.set(firstUser.id, oldSaved);
          oldWrite.resolve(oldSaved);
          await oldWrite.promise;
        } else {
          oldWrite.reject(new ApiError("http", "登录状态已失效。", 401));
          await oldWrite.promise.catch(() => undefined);
        }
      });

      expect(within(secondPanel).getByText("新账号粗剪 · 1")).toBeInTheDocument();
      expect(screen.getByText(secondUser.username)).toBeInTheDocument();
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("rough-cut-edit-second-token");
      expect(saveRoughCut).toHaveBeenCalledTimes(1);

      await user.click(within(secondPanel).getByRole("button", { name: "编辑编排" }));
      const secondEditor = within(secondPanel).getByRole("region", { name: "编辑粗剪编排" });
      await user.click(within(secondEditor).getByRole("button", { name: "排除粗剪：镜头 2" }));
      await user.click(within(secondEditor).getByRole("button", { name: "保存粗剪编排" }));
      expect(await within(secondPanel).findByText("已保存草稿的当前投影 · 版本 5")).toBeInTheDocument();
      expect(saveRoughCut).toHaveBeenCalledTimes(2);
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("rough-cut-edit-second-token");
    },
  );
});
