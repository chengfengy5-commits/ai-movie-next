import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../features/auth/AuthProvider";
import { Workspace } from "./Workspace";
import { createDemoServices, type WorkspaceServices } from "../shared/api/services";
import { demoSeries } from "../features/series/demoSeries";
import {
  API_TOKEN_KEY,
  API_USER_KEY,
  MOCK_SESSION_KEY,
  MOCK_USER_KEY,
} from "../shared/api/storage";
import type { Chapter, StoryboardAsset, User } from "../shared/api/contracts";
import { ApiError } from "../shared/api/errors";
import {
  parsePersonalProductionSnapshot,
  type PersonalProductionNoteUpdate,
  type PersonalProductionSnapshot,
} from "../shared/api/personalProductionNotes";
import type { MyTaskPage } from "../shared/api/myTasks";

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
const chapterId = "note-edit-integration-chapter";
const frameIds = ["note-edit-integration-frame-1", "note-edit-integration-frame-2"] as const;
const integrationChapter: Chapter = {
  id: chapterId,
  series_id: testSeries.id,
  title: "记录编辑集成章节",
  content: frameIds.map((id, index) => ({
    text: `章节镜头 ${index + 1}`,
    original_text: `章节镜头 ${index + 1} 原文`,
    storyboard: [id],
    preview: null,
  })),
  order: 1,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
  lock: null,
};
const integrationAssets: StoryboardAsset[] = frameIds.map((id, index) => ({
  id,
  series_id: testSeries.id,
  chapter_id: chapterId,
  frame_index: index,
  name: `集成素材 ${index + 1}`,
  description: null,
  image_url: null,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
}));

function snapshotFor(chapter: Chapter, note: string, revision = 1): PersonalProductionSnapshot {
  return parsePersonalProductionSnapshot({
    chapter_id: chapter.id,
    revision,
    media_state: "ready",
    frames: frameIds.map((id, frame_index) => ({
      frame_index,
      storyboard_asset_id: id,
      media_revision: 1,
      source_valid: true,
      asset_image_digest: null,
      preview_digest: null,
      invalid_reason: null,
    })),
    frame_notes: {
      [frameIds[0]]: { status: "needs_revision", note: "当前账号的续作记录" },
      [frameIds[1]]: { status: "needs_revision", note },
    },
    resume_frame_id: frameIds[0],
  }, chapter.id);
}

function cloneSnapshot(snapshot: PersonalProductionSnapshot): PersonalProductionSnapshot {
  return {
    ...snapshot,
    frames: snapshot.frames.map((frame) => ({ ...frame })),
    frame_notes: new Map(snapshot.frame_notes),
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

function updatedSnapshot(
  current: PersonalProductionSnapshot,
  update: PersonalProductionNoteUpdate,
): PersonalProductionSnapshot {
  const target = update.frames[0];
  const notes = Object.fromEntries(current.frame_notes.entries());
  notes[target.storyboard_asset_id] = { status: target.status, note: target.note };
  return parsePersonalProductionSnapshot({
    chapter_id: current.chapter_id,
    revision: current.revision + 1,
    media_state: current.media_state,
    frames: current.frames,
    frame_notes: notes,
    resume_frame_id: current.resume_frame_id,
  }, current.chapter_id);
}

function apiNoteEditServices(options: {
  user: User;
  getNotes: WorkspaceServices["getPersonalProductionNotes"];
  saveNote: NonNullable<WorkspaceServices["savePersonalProductionNote"]>;
}): WorkspaceServices {
  return {
    mode: "api",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    restore: async () => options.user,
    login: async () => { throw new ApiError("http", "Not used by this test.", 401); },
    listSeries: async () => demoSeries,
    listMyTeams: async () => [],
    listChapters: async () => [integrationChapter],
    listStoryboardAssets: async () => integrationAssets.map((asset) => ({ ...asset })),
    listCharacters: async () => [],
    listScenes: async () => [],
    listProps: async () => [],
    getPersonalProductionNotes: options.getNotes,
    savePersonalProductionNote: options.saveNote,
    getPersonalRoughCut: async () => { throw new Error("Unexpected rough-cut request."); },
    listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    logout: () => {
      window.localStorage.removeItem(API_TOKEN_KEY);
      window.localStorage.removeItem(API_USER_KEY);
    },
  };
}

function seedApiSession(user: User, token: string): void {
  window.localStorage.setItem(API_TOKEN_KEY, token);
  window.localStorage.setItem(API_USER_KEY, JSON.stringify(user));
}

let scrollProbe: ReturnType<typeof installScrollProbe> | null = null;

afterEach(() => {
  cleanup();
  scrollProbe?.restore();
  scrollProbe = null;
  window.localStorage.clear();
});

beforeEach(() => {
  window.localStorage.clear();
});

describe("personal production note edit in Workspace", () => {
  it("saves an existing demo note, reopens the saved snapshot, and preserves navigation/list state", async () => {
    const base = createDemoServices(window.localStorage);
    const read = vi.fn(base.getPersonalProductionNotes.bind(base));
    const saveMethod = base.savePersonalProductionNote;
    if (saveMethod === undefined) {
      throw new Error("Demo service must provide the save capability.");
    }
    const save = vi.fn(saveMethod.bind(base));
    const listChapters = vi.fn(base.listChapters.bind(base));
    const listStoryboardAssets = vi.fn(base.listStoryboardAssets.bind(base));
    const listMyTasks = vi.fn<WorkspaceServices["listMyTasks"]>(async (): Promise<MyTaskPage> => ({
      total: 0,
      page: 1,
      page_size: 10,
      tasks: [],
    }));
    const services: WorkspaceServices = {
      ...base,
      getPersonalProductionNotes: read,
      savePersonalProductionNote: save,
      listChapters,
      listStoryboardAssets,
      listMyTasks,
    };
    window.localStorage.setItem(MOCK_SESSION_KEY, "demo-session");
    window.localStorage.setItem(MOCK_USER_KEY, JSON.stringify(demoUser));
    const user = userEvent.setup();
    scrollProbe = installScrollProbe();
    renderWorkspace(services);

    const list = await screen.findByRole("region", { name: "我的剧集" });
    await user.click(within(list).getByRole("button", { name: "团队剧集" }));
    await user.click(within(list).getByRole("button", { name: /全部剧集/ }));
    await user.click(within(list).getByRole("button", { name: /加载更多/ }));
    expect(within(list).getByText("显示 24 / 24 部")).toBeInTheDocument();

    const seriesCard = within(list).getByRole("heading", { name: testSeries.name }).closest("article");
    if (!(seriesCard instanceof HTMLElement)) {
      throw new Error("The demo series card was not rendered.");
    }
    await user.click(within(seriesCard).getByRole("button", { name: /只读查看章节/ }));
    const chapterRegion = await screen.findByRole("region", { name: testSeries.name });
    await user.click(within(chapterRegion).getByRole("button", { name: "查看我的制作记录" }));
    const notes = await within(chapterRegion).findByRole("region", { name: "我的制作记录" });
    expect(await within(notes).findByText("信封封口处的雨痕需要补充表现。")).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(1);

    await user.click(within(notes).getByRole("button", { name: "编辑镜头 2 的个人记录" }));
    const editor = within(notes).getByRole("region", { name: "编辑镜头 2 的个人记录" });
    const status = within(editor).getByRole("combobox", { name: "镜头 2 的制作状态" });
    const note = within(editor).getByRole("textbox", { name: "镜头 2 的文字备注" });
    await user.selectOptions(status, "unmarked");
    await user.clear(note);
    await user.paste("这条文字由真实 Workspace 编辑器保存。");
    await user.click(within(editor).getByRole("button", { name: "保存镜头记录" }));

    expect(await within(notes).findByText("这条文字由真实 Workspace 编辑器保存。")).toBeInTheDocument();
    const savedRow = within(notes)
      .getByRole("button", { name: "编辑镜头 2 的个人记录" })
      .closest("li");
    if (!(savedRow instanceof HTMLElement)) {
      throw new Error("The saved note row was not rendered.");
    }
    expect(within(savedRow).getByText("未标记")).toBeInTheDocument();
    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0]?.[0]).toBe(testSeries.id + "-chapter-opening");
    expect(read).toHaveBeenCalledTimes(1);
    expect(within(notes).getByRole("button", { name: "定位到记录镜头 2" })).toBeInTheDocument();
    expect(within(chapterRegion).getByRole("button", { name: "定位到续作镜头" })).toBeInTheDocument();

    await user.click(within(notes).getByRole("button", { name: "关闭记录" }));
    await user.click(within(chapterRegion).getByRole("button", { name: "查看我的制作记录" }));
    const reopenedNotes = await within(chapterRegion).findByRole("region", { name: "我的制作记录" });
    expect(await within(reopenedNotes).findByText("这条文字由真实 Workspace 编辑器保存。")).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(2);

    const filter = within(reopenedNotes).getByRole("group", { name: "按状态筛选" });
    const unmarked = within(filter).getByRole("button", { name: "未标记 1" });
    await user.click(unmarked);
    expect(unmarked).toHaveAttribute("aria-pressed", "true");
    const target = within(chapterRegion).getByRole("article", { name: "第一章 · 雾起 · 镜头 2" });
    await user.click(within(reopenedNotes).getByRole("button", { name: "定位到记录镜头 2" }));
    await waitFor(() => expect(target).toHaveFocus());
    expect(scrollProbe?.targets).toContain(target);
    expect(within(chapterRegion).getByText("已定位到记录镜头 2。")).toBeInTheDocument();
    expect(unmarked).toHaveAttribute("aria-pressed", "true");
    expect(read).toHaveBeenCalledTimes(2);
    expect(save).toHaveBeenCalledTimes(1);
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);

    await user.click(within(chapterRegion).getByRole("button", { name: "定位到续作镜头" }));
    const resumeTarget = within(chapterRegion).getByRole("article", { name: "第一章 · 雾起 · 镜头 1" });
    await waitFor(() => expect(resumeTarget).toHaveFocus());
    expect(scrollProbe?.targets).toContain(resumeTarget);
    expect(within(reopenedNotes).getByRole("heading", { name: "我的制作记录" })).toBeInTheDocument();

    await user.click(within(screen.getByRole("navigation", { name: "主要导航" }))
      .getByRole("button", { name: "我的任务" }));
    expect(await screen.findByRole("heading", { name: "我的任务" })).toBeInTheDocument();
    await waitFor(() => expect(listMyTasks).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
    const returnedList = await screen.findByRole("region", { name: "我的剧集" });
    expect(within(returnedList).getByText("显示 24 / 24 部")).toBeInTheDocument();
    expect(within(returnedList).getByRole("button", { name: "团队剧集" })).toHaveAttribute("aria-pressed", "false");
    expect(within(returnedList).getByRole("button", { name: /全部剧集/ })).toHaveAttribute("aria-pressed", "true");
    expect(listMyTasks).toHaveBeenCalledTimes(1);
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("creates the first revision-zero demo record and restores it through the real Workspace", async () => {
    window.localStorage.setItem(MOCK_SESSION_KEY, "demo-session");
    window.localStorage.setItem(MOCK_USER_KEY, JSON.stringify(demoUser));
    const base = createDemoServices(window.localStorage);
    const signal = new AbortController().signal;
    const chapters = await base.listChapters(testSeries.id, signal);
    const unsavedChapter = chapters.find((chapter) => chapter.title === "第二章 · 来信");
    if (unsavedChapter === undefined) {
      throw new Error("The demo revision-zero chapter was not available.");
    }
    const initialSnapshot = await base.getPersonalProductionNotes(unsavedChapter.id, signal);
    expect(initialSnapshot.revision).toBe(0);
    expect(initialSnapshot.frame_notes.size).toBe(0);
    expect(initialSnapshot.frames).toHaveLength(1);

    const read = vi.fn(base.getPersonalProductionNotes.bind(base));
    const saveMethod = base.savePersonalProductionNote;
    if (saveMethod === undefined) {
      throw new Error("Demo service must provide the save capability.");
    }
    const save = vi.fn(saveMethod.bind(base));
    const listChapters = vi.fn(base.listChapters.bind(base));
    const listStoryboardAssets = vi.fn(base.listStoryboardAssets.bind(base));
    const services: WorkspaceServices = {
      ...base,
      getPersonalProductionNotes: read,
      savePersonalProductionNote: save,
      listChapters,
      listStoryboardAssets,
    };
    const user = userEvent.setup();
    scrollProbe = installScrollProbe();
    renderWorkspace(services);

    const seriesList = await screen.findByRole("region", { name: "我的剧集" });
    const seriesHeading = await within(seriesList).findByRole("heading", { name: testSeries.name });
    const seriesCard = seriesHeading.closest("article");
    if (!(seriesCard instanceof HTMLElement)) {
      throw new Error("The demo series card was not rendered.");
    }
    await user.click(within(seriesCard).getByRole("button", { name: /只读查看章节/ }));
    const chapterRegion = await screen.findByRole("region", { name: testSeries.name });
    await user.click(within(chapterRegion).getByRole("button", { name: /第二章 · 来信/ }));
    const target = await within(chapterRegion).findByRole("article", { name: "第二章 · 来信 · 镜头 1" });
    await user.click(within(chapterRegion).getByRole("button", { name: "查看我的制作记录" }));
    const notes = await within(chapterRegion).findByRole("region", { name: "我的制作记录" });
    expect(await within(notes).findByText("你还没有保存个人制作记录。")).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(1);

    await user.click(within(notes).getByRole("button", { name: "新增镜头 1 的个人记录" }));
    const editor = within(notes).getByRole("region", { name: "编辑镜头 1 的个人记录" });
    await user.selectOptions(within(editor).getByRole("combobox", { name: "镜头 1 的制作状态" }), "needs_revision");
    await user.click(within(editor).getByRole("textbox", { name: "镜头 1 的文字备注" }));
    await user.paste("第二章首次建立记录");
    await user.click(within(editor).getByRole("button", { name: "保存镜头记录" }));

    expect(await within(notes).findByText("第二章首次建立记录")).toBeInTheDocument();
    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0]?.[1]?.expected_revision).toBe(0);
    expect(read).toHaveBeenCalledTimes(1);
    await user.click(within(notes).getByRole("button", { name: "定位到记录镜头 1" }));
    await waitFor(() => expect(target).toHaveFocus());
    expect(scrollProbe?.targets).toContain(target);

    await user.click(within(notes).getByRole("button", { name: "关闭记录" }));
    await user.click(within(chapterRegion).getByRole("button", { name: "查看我的制作记录" }));
    const reopenedNotes = await within(chapterRegion).findByRole("region", { name: "我的制作记录" });
    expect(await within(reopenedNotes).findByText("第二章首次建立记录")).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(2);
    expect(save).toHaveBeenCalledTimes(1);
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(2);
  });

  it("keeps a conflicted draft behind the read gate and restores the parent entry after close and reopen", async () => {
    const user = authenticatedUser("note-edit-conflict-user", "冲突恢复账号");
    let currentSnapshot = snapshotFor(integrationChapter, "服务器初始记录", 1);
    const read = vi.fn(async (requestedChapterId: string, _signal: AbortSignal) => {
      if (requestedChapterId !== chapterId) {
        throw new Error("Unexpected chapter id for personal notes.");
      }
      return cloneSnapshot(currentSnapshot);
    });
    let attempts = 0;
    const save = vi.fn(async (
      requestedChapterId: string,
      update: PersonalProductionNoteUpdate,
      _signal: AbortSignal,
    ) => {
      if (requestedChapterId !== chapterId) {
        throw new Error("Unexpected chapter id for personal notes.");
      }
      attempts += 1;
      if (attempts < 3) {
        currentSnapshot = snapshotFor(integrationChapter, `服务器并发版本 ${attempts}`, currentSnapshot.revision + 1);
        throw new ApiError("http", "个人记录版本已变化，请重新读取。", 409, "个人记录版本已变化，请重新读取。");
      }
      currentSnapshot = updatedSnapshot(currentSnapshot, update);
      return cloneSnapshot(currentSnapshot);
    });
    seedApiSession(user, "note-edit-conflict-session-token");
    const services = apiNoteEditServices({ user, getNotes: read, saveNote: save });
    const userActions = userEvent.setup();
    scrollProbe = installScrollProbe();
    renderWorkspace(services);

    await screen.findByText(user.username);
    const seriesHeading = await screen.findByRole("heading", { name: testSeries.name });
    const seriesCard = seriesHeading.closest("article");
    if (!(seriesCard instanceof HTMLElement)) {
      throw new Error("The series card was not rendered.");
    }
    await userActions.click(within(seriesCard).getByRole("button", { name: /只读查看章节/ }));
    const chapterRegion = await screen.findByRole("region", { name: testSeries.name });
    await userActions.click(within(chapterRegion).getByRole("button", { name: "查看我的制作记录" }));
    let notes = await within(chapterRegion).findByRole("region", { name: "我的制作记录" });
    expect(await within(notes).findByText("服务器初始记录")).toBeInTheDocument();

    await userActions.click(within(notes).getByRole("button", { name: "编辑镜头 2 的个人记录" }));
    let editor = within(notes).getByRole("region", { name: "编辑镜头 2 的个人记录" });
    const note = within(editor).getByRole("textbox", { name: "镜头 2 的文字备注" });
    await userActions.clear(note);
    await userActions.paste("保留在本地的冲突草稿");
    await userActions.click(within(editor).getByRole("button", { name: "保存镜头记录" }));
    let conflict = await within(notes).findByRole("alert");
    expect(within(conflict).getByText(/文字备注：保留在本地的冲突草稿/)).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledTimes(1);

    await userActions.click(within(conflict).getByRole("button", { name: "重新读取并核实" }));
    expect(await within(notes).findByText("服务器并发版本 1")).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(2);
    await userActions.click(within(notes).getByRole("button", { name: "编辑镜头 2 的个人记录" }));
    editor = within(notes).getByRole("region", { name: "编辑镜头 2 的个人记录" });
    expect(within(editor).getByRole("textbox", { name: "镜头 2 的文字备注" })).toHaveValue("保留在本地的冲突草稿");
    const recoveredStatus = within(editor).getByRole("combobox", { name: "镜头 2 的制作状态" });
    expect(recoveredStatus).toHaveValue("");
    await userActions.selectOptions(recoveredStatus, "needs_revision");
    await userActions.click(within(editor).getByRole("button", { name: "保存镜头记录" }));
    conflict = await within(notes).findByRole("alert");
    expect(within(conflict).getByText(/文字备注：保留在本地的冲突草稿/)).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(2);
    expect(save).toHaveBeenCalledTimes(2);

    await userActions.click(within(notes).getByRole("button", { name: "关闭记录" }));
    await waitFor(() => expect(within(chapterRegion).queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument());
    await userActions.click(within(chapterRegion).getByRole("button", { name: "查看我的制作记录" }));
    notes = await within(chapterRegion).findByRole("region", { name: "我的制作记录" });
    expect(await within(notes).findByText("服务器并发版本 2")).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(3);
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("note-edit-conflict-session-token");

    await userActions.click(within(notes).getByRole("button", { name: "编辑镜头 2 的个人记录" }));
    editor = within(notes).getByRole("region", { name: "编辑镜头 2 的个人记录" });
    await userActions.clear(within(editor).getByRole("textbox", { name: "镜头 2 的文字备注" }));
    await userActions.paste("显式重读后再次保存");
    await userActions.click(within(editor).getByRole("button", { name: "保存镜头记录" }));
    expect(await within(notes).findByText("显式重读后再次保存")).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(3);
    expect(save).toHaveBeenCalledTimes(3);
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("note-edit-conflict-session-token");
  });
  it.each(["success", "401"] as const)(
    "ignores a late save %s from the logged-out account and keeps the new account usable",
    async (lateResult) => {
      const firstUser = authenticatedUser("note-edit-first-user", "旧制作记录账号");
      const secondUser = authenticatedUser("note-edit-second-user", "新制作记录账号");
      const current = new Map<string, PersonalProductionSnapshot>([
        [firstUser.id, snapshotFor(integrationChapter, "旧账号镜头备注")],
        [secondUser.id, snapshotFor(integrationChapter, "新账号私人镜头备注")],
      ]);
      const staleSave = deferred<PersonalProductionSnapshot>();
      let activeUser = firstUser;
      const read = vi.fn(async (requestedChapterId: string) => {
        if (requestedChapterId !== chapterId) {
          throw new Error("Unexpected chapter id for personal notes.");
        }
        return cloneSnapshot(current.get(activeUser.id)!);
      });
      const save = vi.fn((requestedChapterId: string, _update: PersonalProductionNoteUpdate, _signal: AbortSignal) => {
        if (requestedChapterId !== chapterId || activeUser.id !== firstUser.id) {
          throw new Error("Only the first account should start the deferred save.");
        }
        // Deliberately ignore AbortSignal so the original save promise can settle after logout.
        return staleSave.promise;
      });
      const logout = vi.fn(() => {
        activeUser = firstUser;
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      });
      const services: WorkspaceServices = {
        mode: "api",
        apiBaseUrl: "http://127.0.0.1:4175/api",
        restore: async () => firstUser,
        login: async ({ username }) => {
          if (username !== "second") {
            throw new ApiError("http", "账号或密码不正确。", 401);
          }
          activeUser = secondUser;
          window.localStorage.setItem(API_TOKEN_KEY, "note-edit-second-session-token");
          window.localStorage.setItem(API_USER_KEY, JSON.stringify(secondUser));
          return secondUser;
        },
        listSeries: async () => demoSeries,
        listMyTeams: async () => [],
        listChapters: async () => [integrationChapter],
        listStoryboardAssets: async () => integrationAssets.map((asset) => ({ ...asset })),
        listCharacters: async () => [],
        listScenes: async () => [],
        listProps: async () => [],
        getPersonalProductionNotes: read,
        savePersonalProductionNote: save,
        getPersonalRoughCut: async () => { throw new Error("Unexpected rough-cut request."); },
        listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
        logout,
      };
      window.localStorage.setItem(API_TOKEN_KEY, "note-edit-first-session-token");
      window.localStorage.setItem(API_USER_KEY, JSON.stringify(firstUser));
      const user = userEvent.setup();
      scrollProbe = installScrollProbe();
      renderWorkspace(services);

      await screen.findByText(firstUser.username);
      await screen.findAllByRole("article");
      const firstCard = screen.getByRole("heading", { name: testSeries.name }).closest("article");
      if (!(firstCard instanceof HTMLElement)) {
        throw new Error("The first series card was not rendered.");
      }
      await user.click(within(firstCard).getByRole("button", { name: /只读查看章节/ }));
      const firstChapterRegion = await screen.findByRole("region", { name: testSeries.name });
      await user.click(within(firstChapterRegion).getByRole("button", { name: "查看我的制作记录" }));
      const firstNotes = await within(firstChapterRegion).findByRole("region", { name: "我的制作记录" });
      expect(await within(firstNotes).findByText("旧账号镜头备注")).toBeInTheDocument();
      await user.click(within(firstNotes).getByRole("button", { name: "编辑镜头 2 的个人记录" }));
      const editor = within(firstNotes).getByRole("region", { name: "编辑镜头 2 的个人记录" });
      await user.clear(within(editor).getByRole("textbox", { name: "镜头 2 的文字备注" }));
      await user.paste("迟到保存内容");
      await user.click(within(editor).getByRole("button", { name: "保存镜头记录" }));
      await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
      expect(screen.getByText("正在保存镜头 2 的个人记录…")).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "退出登录" }));
      const loginCard = await screen.findByRole("region", { name: "欢迎回来" });
      await user.click(within(loginCard).getByLabelText("账号"));
      await user.paste("second");
      await user.click(within(loginCard).getByLabelText("密码"));
      await user.paste("pass");
      await user.click(within(loginCard).getByRole("button", { name: "登录工作台" }));
      expect(await screen.findByText(secondUser.username)).toBeInTheDocument();
      await screen.findAllByRole("article");
      const secondCard = screen.getByRole("heading", { name: testSeries.name }).closest("article");
      if (!(secondCard instanceof HTMLElement)) {
        throw new Error("The second account series card was not rendered.");
      }
      await user.click(within(secondCard).getByRole("button", { name: /只读查看章节/ }));
      const secondChapterRegion = await screen.findByRole("region", { name: testSeries.name });
      await user.click(within(secondChapterRegion).getByRole("button", { name: "查看我的制作记录" }));
      const secondNotes = await within(secondChapterRegion).findByRole("region", { name: "我的制作记录" });
      expect(await within(secondNotes).findByText("新账号私人镜头备注")).toBeInTheDocument();

      await act(async () => {
        if (lateResult === "success") {
          const firstSnapshot = current.get(firstUser.id)!;
          const update = save.mock.calls[0]?.[1];
          if (update === undefined) {
            throw new Error("The save request did not retain its submitted patch.");
          }
          const committed = updatedSnapshot(firstSnapshot, update);
          current.set(firstUser.id, committed);
          staleSave.resolve(committed);
          await staleSave.promise;
        } else {
          staleSave.reject(new ApiError("http", "登录状态已失效。", 401));
          await staleSave.promise.catch(() => undefined);
        }
      });

      expect(within(secondNotes).getByText("新账号私人镜头备注")).toBeInTheDocument();
      expect(within(secondNotes).queryByText("迟到保存内容")).not.toBeInTheDocument();
      expect(screen.getByText(secondUser.username)).toBeInTheDocument();
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("note-edit-second-session-token");
      expect(logout).toHaveBeenCalledTimes(1);
      expect(save).toHaveBeenCalledTimes(1);
      expect(read).toHaveBeenCalledTimes(2);

      const target = within(secondChapterRegion).getByRole("article", { name: "记录编辑集成章节 · 镜头 2" });
      await user.click(within(secondNotes).getByRole("button", { name: "定位到记录镜头 2" }));
      await waitFor(() => expect(target).toHaveFocus());
      expect(scrollProbe?.targets).toContain(target);
      expect(within(secondChapterRegion).getByText("已定位到记录镜头 2。")).toBeInTheDocument();
      expect(within(secondNotes).getByText("新账号私人镜头备注")).toBeInTheDocument();
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("note-edit-second-session-token");
      expect(logout).toHaveBeenCalledTimes(1);
      expect(read).toHaveBeenCalledTimes(2);
    },
  );
});
