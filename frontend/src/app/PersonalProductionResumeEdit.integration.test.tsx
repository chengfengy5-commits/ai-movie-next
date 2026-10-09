import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../features/auth/AuthProvider";
import { Workspace } from "./Workspace";
import { createDemoServices, type WorkspaceServices } from "../shared/api/services";
import { demoSeries } from "../features/series/demoSeries";
import { getDemoChapterData } from "../features/chapters/demoChapters";
import { getDemoPersonalProductionNotes } from "../features/chapters/demoPersonalProductionNotes";
import {
  API_TOKEN_KEY,
  API_USER_KEY,
  MOCK_SESSION_KEY,
  MOCK_USER_KEY,
} from "../shared/api/storage";
import type { Chapter, StoryboardAsset, User } from "../shared/api/contracts";
import { ApiError } from "../shared/api/errors";
import type {
  PersonalProductionResumeUpdate,
  PersonalProductionSnapshot,
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
const chapterData = getDemoChapterData(testSeries.id)!;
const openingChapter = chapterData.chapters[0]!;
const revisionZeroChapter = chapterData.chapters[1]!;
const openingAssets = chapterData.assetsByChapter[openingChapter.id]!;

function cloneSnapshot(snapshot: PersonalProductionSnapshot): PersonalProductionSnapshot {
  return {
    ...snapshot,
    frames: snapshot.frames.map((frame) => ({ ...frame })),
    frame_notes: new Map([...snapshot.frame_notes.entries()].map(([id, note]) => [
      id,
      note !== null && typeof note === "object" && !Array.isArray(note) ? { ...note } : note,
    ])),
  };
}

function firstStoryboardAssetId(chapter: Chapter, framePosition: number): string | null {
  const frame = chapter.content?.[framePosition];
  if (frame === undefined) {
    return null;
  }
  const storyboard = frame.storyboard;
  if (!Array.isArray(storyboard)) {
    return null;
  }
  const assetId = storyboard[0];
  return typeof assetId === "string" ? assetId : null;
}

function snapshotFor(
  chapter: Chapter,
  assets: StoryboardAsset[],
  revision: number,
  resumeFrameId: string | null,
): PersonalProductionSnapshot {
  const snapshot = getDemoPersonalProductionNotes(chapter.id, chapter, assets);
  return {
    ...cloneSnapshot(snapshot),
    revision,
    resume_frame_id: resumeFrameId,
  };
}

function setResumeSnapshot(
  snapshot: PersonalProductionSnapshot,
  update: PersonalProductionResumeUpdate,
): PersonalProductionSnapshot {
  return {
    ...cloneSnapshot(snapshot),
    revision: snapshot.revision + 1,
    resume_frame_id: update.resume_frame_id,
  };
}

function renderWorkspace(services: WorkspaceServices) {
  return render(
    <AuthProvider services={services}>
      <Workspace />
    </AuthProvider>,
  );
}

function seedDemoSession(): void {
  window.localStorage.setItem(MOCK_SESSION_KEY, "demo-session");
  window.localStorage.setItem(MOCK_USER_KEY, JSON.stringify(demoUser));
}

function authenticatedUser(id: string, username: string): User {
  return { ...demoUser, id, username, membership_type: "free" };
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

describe("personal production resume edits in Workspace", () => {
  it("creates and clears a revision-zero position, then preserves list filters and expansion through tasks", async () => {
    const base = createDemoServices(window.localStorage);
    const getNotes = vi.fn(base.getPersonalProductionNotes.bind(base));
    const saveResumeMethod = base.savePersonalProductionResume;
    if (saveResumeMethod === undefined) {
      throw new Error("Demo services must expose resume-position saving.");
    }
    const saveResume = vi.fn(saveResumeMethod.bind(base));
    const listChapters = vi.fn(base.listChapters.bind(base));
    const listAssets = vi.fn(base.listStoryboardAssets.bind(base));
    const listMyTasks = vi.fn<WorkspaceServices["listMyTasks"]>(async (): Promise<MyTaskPage> => ({
      total: 0,
      page: 1,
      page_size: 10,
      tasks: [],
    }));
    const services: WorkspaceServices = {
      ...base,
      getPersonalProductionNotes: getNotes,
      savePersonalProductionResume: saveResume,
      listChapters,
      listStoryboardAssets: listAssets,
      listMyTasks,
    };
    seedDemoSession();
    const user = userEvent.setup();
    scrollProbe = installScrollProbe();
    renderWorkspace(services);

    const seriesList = await screen.findByRole("region", { name: "我的剧集" });
    await user.click(within(seriesList).getByRole("button", { name: "团队剧集" }));
    await user.click(within(seriesList).getByRole("button", { name: /全部剧集/ }));
    await user.click(within(seriesList).getByRole("button", { name: /加载更多/ }));
    expect(within(seriesList).getByText("显示 24 / 24 部")).toBeInTheDocument();

    const seriesCard = within(seriesList).getByRole("heading", { name: testSeries.name }).closest("article");
    if (!(seriesCard instanceof HTMLElement)) {
      throw new Error("The demo series card was not rendered.");
    }
    await user.click(within(seriesCard).getByRole("button", { name: /只读查看章节/ }));
    const chapterRegion = await screen.findByRole("region", { name: testSeries.name });
    await user.click(within(chapterRegion).getByRole("button", { name: /第二章 · 来信/ }));
    await user.click(within(chapterRegion).getByRole("button", { name: "查看我的制作记录" }));
    let notes = await within(chapterRegion).findByRole("region", { name: "我的制作记录" });
    expect(await within(notes).findByText("你还没有保存个人制作记录。")).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(1);

    const snapshot = await base.getPersonalProductionNotes(revisionZeroChapter.id, new AbortController().signal);
    const targetId = snapshot.frames[0]?.storyboard_asset_id;
    if (!targetId) {
      throw new Error("The revision-zero chapter lacks a stable target.");
    }
    const selector = within(notes).getByRole("combobox", { name: "选择续作镜头" });
    await user.selectOptions(selector, targetId);
    await user.click(within(notes).getByRole("button", { name: "保存续作位置" }));

    await waitFor(() => expect(saveResume).toHaveBeenCalledTimes(1));
    expect(saveResume.mock.calls[0]?.[0]).toBe(revisionZeroChapter.id);
    expect(saveResume.mock.calls[0]?.[1]).toEqual({
      expected_revision: 0,
      resume_frame_id: targetId,
    });
    expect(getNotes).toHaveBeenCalledTimes(1);
    expect(within(notes).getByText("续作位置：镜头 1。页面不会自动跳转。")).toBeInTheDocument();
    expect(scrollProbe?.targets).toEqual([]);

    await user.click(within(notes).getByRole("button", { name: "清除续作位置" }));
    await waitFor(() => expect(saveResume).toHaveBeenCalledTimes(2));
    expect(saveResume.mock.calls[1]?.[1]).toEqual({ expected_revision: 1, resume_frame_id: null });
    expect(getNotes).toHaveBeenCalledTimes(1);
    expect(within(notes).queryByText("续作位置：镜头 1。页面不会自动跳转。")).not.toBeInTheDocument();

    await user.click(within(notes).getByRole("button", { name: "关闭记录" }));
    await user.click(within(chapterRegion).getByRole("button", { name: "查看我的制作记录" }));
    notes = await within(chapterRegion).findByRole("region", { name: "我的制作记录" });
    expect(getNotes).toHaveBeenCalledTimes(2);
    expect(within(notes).queryByText(/续作位置：/)).not.toBeInTheDocument();

    await user.click(within(screen.getByRole("navigation", { name: "主要导航" }))
      .getByRole("button", { name: "我的任务" }));
    expect(await screen.findByRole("heading", { name: "我的任务" })).toBeInTheDocument();
    await waitFor(() => expect(listMyTasks).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
    const returnedList = await screen.findByRole("region", { name: "我的剧集" });
    expect(within(returnedList).getByText("显示 24 / 24 部")).toBeInTheDocument();
    expect(within(returnedList).getByRole("button", { name: "团队剧集" })).toHaveAttribute("aria-pressed", "false");
    expect(within(returnedList).getByRole("button", { name: /全部剧集/ })).toHaveAttribute("aria-pressed", "true");
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listAssets).toHaveBeenCalledTimes(2);
  });

  it("retains a conflicted resume intent until an explicit reread, then permits a fresh save", async () => {
    const base = createDemoServices(window.localStorage);
    const initialSnapshot = snapshotFor(openingChapter, openingAssets, 4, null);
    const conflictSnapshot = {
      ...cloneSnapshot(initialSnapshot),
      revision: 5,
      resume_frame_id: null,
    };
    let readCount = 0;
    let saveCount = 0;
    const getNotes = vi.fn(async () => {
      readCount += 1;
      return cloneSnapshot(readCount === 1 ? initialSnapshot : conflictSnapshot);
    });
    const targetId = initialSnapshot.frames.find(
      (frame) => frame.storyboard_asset_id !== null && frame.source_valid,
    )?.storyboard_asset_id;
    if (!targetId) {
      throw new Error("The opening chapter lacks an editable resume target.");
    }
    const saveResume = vi.fn(async (
      _chapterId: string,
      update: PersonalProductionResumeUpdate,
    ): Promise<PersonalProductionSnapshot> => {
      saveCount += 1;
      if (saveCount === 1) {
        throw new ApiError("http", "记录版本已变化，请重新读取。", 409);
      }
      return setResumeSnapshot(conflictSnapshot, update);
    });
    const services: WorkspaceServices = {
      ...base,
      getPersonalProductionNotes: getNotes,
      savePersonalProductionResume: saveResume,
    };
    seedDemoSession();
    const user = userEvent.setup();
    renderWorkspace(services);

    const seriesList = await screen.findByRole("region", { name: "我的剧集" });
    const card = (await within(seriesList)
      .findByRole("heading", { name: testSeries.name })).closest("article");
    if (!(card instanceof HTMLElement)) {
      throw new Error("The demo series card was not rendered.");
    }
    await user.click(within(card).getByRole("button", { name: /只读查看章节/ }));
    const chapterRegion = await screen.findByRole("region", { name: testSeries.name });
    await user.click(within(chapterRegion).getByRole("button", { name: /第一章/ }));
    await user.click(within(chapterRegion).getByRole("button", { name: "查看我的制作记录" }));
    const notes = await within(chapterRegion).findByRole("region", { name: "我的制作记录" });
    expect(getNotes).toHaveBeenCalledTimes(1);

    await user.selectOptions(within(notes).getByRole("combobox", { name: "选择续作镜头" }), targetId);
    await user.click(within(notes).getByRole("button", { name: "保存续作位置" }));
    expect(await within(notes).findByRole("alert")).toHaveTextContent("续作位置保存未完成");
    expect(within(notes).getByText("操作意图已保留为只读。请显式重新读取后再决定是否重试。")).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(1);
    expect(saveResume).toHaveBeenCalledTimes(1);

    await user.click(within(notes).getByRole("button", { name: "重新读取并核实" }));
    await waitFor(() => expect(getNotes).toHaveBeenCalledTimes(2));
    expect(within(notes).getByText("当前没有可对应的续作位置。")).toBeInTheDocument();
    expect(within(notes).queryByRole("alert")).not.toBeInTheDocument();

    await user.selectOptions(within(notes).getByRole("combobox", { name: "选择续作镜头" }), targetId);
    await user.click(within(notes).getByRole("button", { name: "保存续作位置" }));
    await waitFor(() => expect(saveResume).toHaveBeenCalledTimes(2));
    expect(saveResume.mock.calls[1]?.[1]).toEqual({
      expected_revision: 5,
      resume_frame_id: targetId,
    });
    expect(within(notes).getByText("续作位置：镜头 1。页面不会自动跳转。")).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(2);
  });

  it("does not write a resume while an edited note is dirty", async () => {
    const base = createDemoServices(window.localStorage);
    const saveResumeMethod = base.savePersonalProductionResume;
    if (saveResumeMethod === undefined) {
      throw new Error("Demo services must expose resume-position saving.");
    }
    const saveResume = vi.fn(saveResumeMethod.bind(base));
    const getNotes = vi.fn(base.getPersonalProductionNotes.bind(base));
    const services: WorkspaceServices = {
      ...base,
      getPersonalProductionNotes: getNotes,
      savePersonalProductionResume: saveResume,
    };
    seedDemoSession();
    const user = userEvent.setup();
    renderWorkspace(services);

    const seriesList = await screen.findByRole("region", { name: "我的剧集" });
    const card = within(seriesList).getByRole("heading", { name: testSeries.name }).closest("article");
    if (!(card instanceof HTMLElement)) {
      throw new Error("The demo series card was not rendered.");
    }
    await user.click(within(card).getByRole("button", { name: /只读查看章节/ }));
    const chapterRegion = await screen.findByRole("region", { name: testSeries.name });
    await user.click(within(chapterRegion).getByRole("button", { name: /第二章 · 来信/ }));
    await user.click(within(chapterRegion).getByRole("button", { name: "查看我的制作记录" }));
    const notes = await within(chapterRegion).findByRole("region", { name: "我的制作记录" });
    const targetId = firstStoryboardAssetId(revisionZeroChapter, 0);
    if (typeof targetId !== "string") {
      throw new Error("The revision-zero chapter lacks a storyboard identity.");
    }

    await user.selectOptions(within(notes).getByRole("combobox", { name: "选择续作镜头" }), targetId);
    const createRecord = within(notes).getByRole("button", { name: "新增镜头 1 的个人记录" });
    await user.click(createRecord);
    const editor = within(notes).getByRole("region", { name: "编辑镜头 1 的个人记录" });
    await user.selectOptions(within(editor).getByRole("combobox", { name: "镜头 1 的制作状态" }), "needs_revision");
    const note = within(editor).getByRole("textbox", { name: "镜头 1 的文字备注" });
    await user.click(note);
    await user.paste("尚未保存的备注草稿");

    const savePosition = within(notes).getByRole("button", { name: "保存续作位置" });
    expect(savePosition).toBeDisabled();
    await user.click(savePosition);
    expect(saveResume).not.toHaveBeenCalled();
    expect(getNotes).toHaveBeenCalledTimes(1);
    expect(within(editor).getByRole("textbox", { name: "镜头 1 的文字备注" })).toHaveValue("尚未保存的备注草稿");
  });

  it.each(["success", "401"] as const)(
    "does not let an old session's late resume PUT %s change a newly logged-in user's record",
    async (lateResult) => {
      const firstUser = authenticatedUser("resume-edit-first-user", "旧续作账号");
      const secondUser = authenticatedUser("resume-edit-second-user", "新续作账号");
      const firstFrameId = firstStoryboardAssetId(openingChapter, 0);
      const secondFrameId = firstStoryboardAssetId(openingChapter, 1);
      if (firstFrameId === null || secondFrameId === null) {
        throw new Error("The opening chapter lacks stable frame IDs.");
      }
      const records = new Map<string, PersonalProductionSnapshot>([
        [firstUser.id, snapshotFor(openingChapter, openingAssets, 4, firstFrameId)],
        [secondUser.id, snapshotFor(openingChapter, openingAssets, 7, null)],
      ]);
      const staleSave = deferred<PersonalProductionSnapshot>();
      let activeUser = firstUser;
      const read = vi.fn(async (requestedChapterId: string) => {
        if (requestedChapterId !== openingChapter.id) {
          throw new Error("Unexpected chapter for resume navigation.");
        }
        return cloneSnapshot(records.get(activeUser.id)!);
      });
      const saveResume = vi.fn(async (
        requestedChapterId: string,
        update: PersonalProductionResumeUpdate,
        _signal: AbortSignal,
      ) => {
        if (requestedChapterId !== openingChapter.id) {
          throw new Error("Unexpected chapter for resume update.");
        }
        if (activeUser.id === firstUser.id) {
          // Intentionally ignore AbortSignal so the old PUT promise settles after logout.
          return staleSave.promise;
        }
        const current = records.get(activeUser.id)!;
        const next = setResumeSnapshot(current, update);
        records.set(activeUser.id, cloneSnapshot(next));
        return cloneSnapshot(next);
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
          window.localStorage.setItem(API_TOKEN_KEY, "resume-edit-second-session-token");
          window.localStorage.setItem(API_USER_KEY, JSON.stringify(secondUser));
          return secondUser;
        },
        listSeries: async () => demoSeries,
        listMyTeams: async () => [],
        listChapters: async () => chapterData.chapters.map((chapter) => ({ ...chapter })),
        listStoryboardAssets: async (_seriesId, requestedChapterId) => (
          (chapterData.assetsByChapter[requestedChapterId] ?? []).map((asset) => ({ ...asset }))
        ),
        listCharacters: async () => [],
        listScenes: async () => [],
        listProps: async () => [],
        getPersonalProductionNotes: read,
        savePersonalProductionResume: saveResume,
        getPersonalRoughCut: async () => { throw new Error("Unexpected rough-cut request."); },
        listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
        logout,
      };
      window.localStorage.setItem(API_TOKEN_KEY, "resume-edit-first-session-token");
      window.localStorage.setItem(API_USER_KEY, JSON.stringify(firstUser));
      const user = userEvent.setup();
      renderWorkspace(services);

      expect(await screen.findByText(firstUser.username)).toBeInTheDocument();
      const firstSeriesList = await screen.findByRole("region", { name: "我的剧集" });
      const firstCard = (await within(firstSeriesList)
        .findByRole("heading", { name: testSeries.name })).closest("article");
      if (!(firstCard instanceof HTMLElement)) {
        throw new Error("The first user's series card was not rendered.");
      }
      await user.click(within(firstCard).getByRole("button", { name: /只读查看章节/ }));
      const firstChapterRegion = await screen.findByRole("region", { name: testSeries.name });
      await user.click(within(firstChapterRegion).getByRole("button", { name: "查看我的制作记录" }));
      const firstNotes = await within(firstChapterRegion).findByRole("region", { name: "我的制作记录" });
      const resumePicker = within(firstNotes).getByRole("combobox", { name: "选择续作镜头" });
      await user.selectOptions(resumePicker, secondFrameId);
      await user.click(within(firstNotes).getByRole("button", { name: "保存续作位置" }));
      await waitFor(() => expect(saveResume).toHaveBeenCalledTimes(1));

      await user.click(screen.getByRole("button", { name: "退出登录" }));
      const login = await screen.findByRole("region", { name: "欢迎回来" });
      await user.click(within(login).getByLabelText("账号"));
      await user.paste("second");
      await user.click(within(login).getByLabelText("密码"));
      await user.paste("pass");
      await user.click(within(login).getByRole("button", { name: "登录工作台" }));
      expect(await screen.findByText(secondUser.username)).toBeInTheDocument();
      const secondSeriesList = await screen.findByRole("region", { name: "我的剧集" });
      const secondCard = (await within(secondSeriesList)
        .findByRole("heading", { name: testSeries.name })).closest("article");
      if (!(secondCard instanceof HTMLElement)) {
        throw new Error("The second user's series card was not rendered.");
      }
      await user.click(within(secondCard).getByRole("button", { name: /只读查看章节/ }));
      const secondChapterRegion = await screen.findByRole("region", { name: testSeries.name });
      await user.click(within(secondChapterRegion).getByRole("button", { name: "查看我的制作记录" }));
      const secondNotes = await within(secondChapterRegion).findByRole("region", { name: "我的制作记录" });
      expect(within(secondNotes).getByText("当前没有可对应的续作位置。")).toBeInTheDocument();

      await act(async () => {
        const oldUpdate = saveResume.mock.calls[0]?.[1];
        if (oldUpdate === undefined) {
          throw new Error("The old PUT did not retain its submitted update.");
        }
        if (lateResult === "success") {
          const oldCurrent = records.get(firstUser.id)!;
          const lateSnapshot = setResumeSnapshot(oldCurrent, oldUpdate);
          records.set(firstUser.id, lateSnapshot);
          staleSave.resolve(cloneSnapshot(lateSnapshot));
          await staleSave.promise;
        } else {
          staleSave.reject(new ApiError("http", "登录状态已失效。", 401));
          await staleSave.promise.catch(() => undefined);
        }
      });

      expect(screen.getByText(secondUser.username)).toBeInTheDocument();
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("resume-edit-second-session-token");
      expect(within(secondNotes).getByText("当前没有可对应的续作位置。")).toBeInTheDocument();
      expect(logout).toHaveBeenCalledTimes(1);
      expect(saveResume).toHaveBeenCalledTimes(1);

      const secondTargetId = firstFrameId;
      await user.selectOptions(within(secondNotes).getByRole("combobox", { name: "选择续作镜头" }), secondTargetId);
      await user.click(within(secondNotes).getByRole("button", { name: "保存续作位置" }));
      await waitFor(() => expect(saveResume).toHaveBeenCalledTimes(2));
      expect(saveResume.mock.calls[1]?.[0]).toBe(openingChapter.id);
      expect(saveResume.mock.calls[1]?.[1]).toMatchObject({
        expected_revision: 7,
        resume_frame_id: secondTargetId,
      });
      expect(within(secondNotes).getByText("续作位置：镜头 1。页面不会自动跳转。")).toBeInTheDocument();
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("resume-edit-second-session-token");
    },
  );
});
