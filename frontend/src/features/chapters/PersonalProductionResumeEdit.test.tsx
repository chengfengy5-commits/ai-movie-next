import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { useLayoutEffect, type ComponentProps } from "react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Chapter, StoryboardAsset } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import {
  parsePersonalProductionSnapshot,
  type PersonalProductionNoteUpdate,
  type PersonalProductionResumeUpdate,
  type PersonalProductionSnapshot,
} from "../../shared/api/personalProductionNotes";
import type { WorkspaceServices } from "../../shared/api/services";
import type { CapturedPersonalProductionMedia, PersonalProductionReadout } from "./personal-production/projection";
import { PersonalProductionNotesPanel } from "./PersonalProductionNotesPanel";

const projectionMocks = vi.hoisted(() => ({ capture: vi.fn(), project: vi.fn() }));
const editorRuntime = vi.hoisted(() => ({
  handlers: [] as Array<{ name: string; invoke: (event: unknown) => void }>,
  capture(_type: unknown, props: unknown) {
    if (typeof props !== "object" || props === null) {
      return;
    }
    const record = props as Record<string, unknown>;
    const label = record["aria-label"];
    if (
      typeof label === "string"
      && typeof record.onChange === "function"
      && (label === "选择续作镜头" || label.includes("文字备注") || label.includes("制作状态"))
    ) {
      this.handlers.push({ name: label, invoke: record.onChange as (event: unknown) => void });
      return;
    }
    if (typeof record.children === "string" && typeof record.onClick === "function") {
      const name = record.children.trim();
      if ([
        "保存镜头记录",
        "关闭编辑器",
        "关闭记录",
        "保存续作位置",
        "清除续作位置",
      ].includes(name)) {
        this.handlers.push({ name, invoke: record.onClick as (event: unknown) => void });
      }
    }
  },
}));

vi.mock("react/jsx-runtime", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react/jsx-runtime")>();
  const capture = (type: unknown, props: unknown) => editorRuntime.capture(type, props);
  return {
    ...actual,
    jsx: (...args: Parameters<typeof actual.jsx>) => {
      capture(args[0], args[1]);
      return actual.jsx(...args);
    },
    jsxs: (...args: Parameters<typeof actual.jsxs>) => {
      capture(args[0], args[1]);
      return actual.jsxs(...args);
    },
  };
});

vi.mock("react/jsx-dev-runtime", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react/jsx-dev-runtime")>();
  return {
    ...actual,
    jsxDEV: (...args: Parameters<typeof actual.jsxDEV>) => {
      editorRuntime.capture(args[0], args[1]);
      return actual.jsxDEV(...args);
    },
  };
});

vi.mock("./personal-production/projection", () => ({
  capturePersonalProductionMedia: projectionMocks.capture,
  projectPersonalProductionSnapshot: projectionMocks.project,
}));

type PanelProps = ComponentProps<typeof PersonalProductionNotesPanel>;

function latestEditorHandler(name: string): (event: unknown) => void {
  const handler = [...editorRuntime.handlers].reverse().find((entry) => entry.name === name);
  if (handler === undefined) {
    throw new Error("Expected to observe the rendered editor handler: " + name);
  }
  return handler.invoke;
}

function valueChangeEvent(value: string): unknown {
  return { currentTarget: { value }, target: { value } };
}

function clickEvent(): MouseEvent {
  return new MouseEvent("click", { bubbles: true });
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


function chapter(id = "chapter-panel"): Chapter {
  return {
    id,
    series_id: "series-panel",
    title: "制作记录面板测试",
    content: [],
    order: 1,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
    lock: null,
  };
}


function storyboardAsset(id: string, chapterId: string): StoryboardAsset {
  return {
    id,
    series_id: "series-panel",
    chapter_id: chapterId,
    frame_index: 0,
    name: "测试素材",
    description: null,
    image_url: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
  };
}


function editableFixture(revision = 0, note = ""): {
  chapter: Chapter;
  assets: StoryboardAsset[];
  captured: CapturedPersonalProductionMedia;
  snapshot: PersonalProductionSnapshot;
  readout: PersonalProductionReadout;
} {
  const currentChapter: Chapter = {
    ...chapter("chapter-editable"),
    content: [{ text: "待编辑镜头", storyboard: ["editable-frame"], preview: null }],
  };
  const currentAssets = [storyboardAsset("editable-frame", currentChapter.id)];
  const captured: CapturedPersonalProductionMedia = {
    state: "ready",
    frames: [{
      frameIndex: 0,
      candidateId: "editable-frame",
      storyboardAssetId: "editable-frame",
      identityValid: true,
      assetImageUrl: null,
      previewUrl: null,
      assetImageDigest: null,
      previewDigest: null,
      digestAvailable: true,
    }],
  };
  const currentSnapshot = parsePersonalProductionSnapshot({
    chapter_id: currentChapter.id,
    revision,
    media_state: "ready",
    frames: [{
      frame_index: 0,
      storyboard_asset_id: "editable-frame",
      media_revision: 1,
      source_valid: true,
      asset_image_digest: null,
      preview_digest: null,
      invalid_reason: null,
    }],
    frame_notes: revision === 0 ? {} : {
      "editable-frame": { status: "needs_revision", note },
    },
    resume_frame_id: null,
  }, currentChapter.id);
  const hasSavedNote = currentSnapshot.frame_notes.has("editable-frame");
  const projected: PersonalProductionReadout = {
    revision: currentSnapshot.revision,
    noSavedRecord: currentSnapshot.revision === 0,
    mediaState: "ready",
    frames: [{
      position: 1,
      status: hasSavedNote ? "needs_revision" : "unmarked",
      note,
      hasSavedNote,
      verified: true,
    }],
    orphanNotes: [],
    resumePosition: null,
    resumeIsInvalid: false,
  };

  return {
    chapter: currentChapter,
    assets: currentAssets,
    captured,
    snapshot: currentSnapshot,
    readout: projected,
  };
}


function projectEditableSnapshot(snapshotValue: PersonalProductionSnapshot): PersonalProductionReadout {
  const noteValue = snapshotValue.frame_notes.get("editable-frame");
  const note = savedNoteText(noteValue);
  const hasSavedNote = snapshotValue.frame_notes.has("editable-frame");
  const status = typeof noteValue === "object" && noteValue !== null && !Array.isArray(noteValue)
    && (noteValue as Record<string, unknown>).status === "needs_revision"
    ? "needs_revision"
    : "unmarked";
  const resumeFrameIndex = snapshotValue.resume_frame_id === null
    ? -1
    : snapshotValue.frames.findIndex((frame) => (
        frame.storyboard_asset_id === snapshotValue.resume_frame_id && frame.source_valid
      ));
  const resumePosition = resumeFrameIndex < 0 ? null : resumeFrameIndex + 1;
  return {
    revision: snapshotValue.revision,
    noSavedRecord: snapshotValue.revision === 0,
    mediaState: "ready",
    frames: [{ position: 1, status, note, hasSavedNote, verified: true }],
    orphanNotes: [],
    resumePosition,
    resumeIsInvalid: snapshotValue.resume_frame_id !== null && resumePosition === null,
  };
}


function updateEditableSnapshot(
  source: PersonalProductionSnapshot,
  update: PersonalProductionNoteUpdate,
): PersonalProductionSnapshot {
  const patch = update.frames[0];
  const frameNotes = new Map(source.frame_notes);
  frameNotes.set(patch.storyboard_asset_id, {
    status: patch.status,
    note: patch.note,
    approved_media_revision: patch.status === "approved" ? patch.expected_media_revision : null,
    needs_reconfirmation: false,
  });
  return {
    ...source,
    revision: source.revision + 1,
    frames: source.frames.map((frame) => ({ ...frame })),
    frame_notes: frameNotes,
  };
}

function updateResumeSnapshot(
  source: PersonalProductionSnapshot,
  update: PersonalProductionResumeUpdate,
): PersonalProductionSnapshot {
  return {
    ...source,
    revision: source.revision + 1,
    frames: source.frames.map((frame) => ({ ...frame })),
    frame_notes: new Map(source.frame_notes),
    resume_frame_id: update.resume_frame_id,
  };
}


interface ReadoutOptions {
  mediaState?: PersonalProductionReadout["mediaState"];
  resumePosition?: number | null;
  resumeIsInvalid?: boolean;
  status?: PersonalProductionReadout["frames"][number]["status"];
  verified?: boolean;
  noSavedRecord?: boolean;
  frames?: PersonalProductionReadout["frames"];
}

function readout(note: string, options: ReadoutOptions = {}): PersonalProductionReadout {
  const mediaState = options.mediaState ?? "empty";
  const includeFrame = mediaState === "ready";
  return {
    revision: 1,
    noSavedRecord: options.noSavedRecord ?? false,
    mediaState,
    frames: options.frames ?? (includeFrame ? [{
      position: 1,
      status: options.status ?? "approved",
      note,
      hasSavedNote: true,
      verified: options.verified ?? true,
    }] : []),
    orphanNotes: [{
      projection: {
        kind: "readable",
        value: {
          status: "needs_revision",
          note,
          approvedMediaRevision: null,
          needsReconfirmation: false,
        },
      },
    }],
    resumePosition: options.resumePosition ?? null,
    resumeIsInvalid: options.resumeIsInvalid ?? false,
  };
}


function savedNoteText(value: unknown): string {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return "";
  }
  const note = (value as Record<string, unknown>).note;
  return typeof note === "string" ? note : "";
}


function servicesFor(getNotes: WorkspaceServices["getPersonalProductionNotes"]): WorkspaceServices {
  return {
    mode: "api",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    restore: async () => null,
    login: async () => { throw new Error("Not used by this test."); },
    listSeries: async () => [],
    listMyTeams: async () => [],
    listChapters: async () => [],
    listStoryboardAssets: async () => [],
    listCharacters: async () => [],
    listScenes: async () => [],
    listProps: async () => [],
    getPersonalProductionNotes: getNotes,
    getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
    listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    logout: vi.fn(),
  };
}


function panelProps(
  services: WorkspaceServices,
  overrides: Partial<PanelProps> = {},
): PanelProps {
  return {
    contextToken: {},
    assetSnapshotToken: {},
    generation: 1,
    assets: [],
    chapter: chapter(),
    mediaSnapshotAvailable: true,
    services,
    userId: "current-user",
    seriesId: "series-panel",
    onClose: vi.fn(),
    onRetryChapter: vi.fn(),
    onUnauthorized: vi.fn(),
    onLocateResume: vi.fn(),
    onResumeInvalidated: vi.fn(),
    onRegisterFrameRead: vi.fn(),
    onFrameReadInvalidated: vi.fn(),
    onLocateProductionNoteFrame: vi.fn(() => true),
    ...overrides,
  };
}

function PanelCommitObserver({ onCommit }: { onCommit: () => void }) {
  useLayoutEffect(() => {
    onCommit();
  });
  return null;
}

beforeEach(() => {
  projectionMocks.capture.mockReset();
  projectionMocks.project.mockReset();
  editorRuntime.handlers.length = 0;
  projectionMocks.capture.mockResolvedValue({ state: "empty", frames: [] } satisfies CapturedPersonalProductionMedia);
  projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));
});

afterEach(() => cleanup());

describe("personal production resume panel", () => {
  it("sets a revision-zero resume target without exposing note rows or locating automatically", async () => {
    const user = userEvent.setup();
    const fixture = editableFixture();
    const getNotes = vi.fn().mockResolvedValue(fixture.snapshot);
    const saveResume = vi.fn(async (_chapterId: string, update: PersonalProductionResumeUpdate) => (
      updateResumeSnapshot(fixture.snapshot, update)
    ));
    const onLocateResume = vi.fn();
    projectionMocks.capture.mockResolvedValue(fixture.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    render(
      <PersonalProductionNotesPanel
        {...panelProps({
          ...servicesFor(getNotes),
          savePersonalProductionResume: saveResume,
        }, {
          chapter: fixture.chapter,
          assets: fixture.assets,
          onLocateResume,
        })}
      />,
    );

    expect(await screen.findByText("你还没有保存个人制作记录。")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "当前镜头制作记录" })).not.toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "选择续作镜头" })).toBeEnabled();
    const target = screen.getByRole("combobox", { name: "选择续作镜头" });
    await user.selectOptions(target, "editable-frame");
    expect(screen.getByRole("button", { name: "保存续作位置" })).toBeEnabled();


    await user.click(screen.getByRole("button", { name: "保存续作位置" }));
    expect(await screen.findByText("续作位置：镜头 1。页面不会自动跳转。")).toBeInTheDocument();
    expect(saveResume).toHaveBeenCalledTimes(1);
    expect(saveResume.mock.calls[0]?.[0]).toBe(fixture.chapter.id);
    expect(saveResume.mock.calls[0]?.[1]).toEqual({
      expected_revision: 0,
      resume_frame_id: "editable-frame",
    });
    expect(getNotes).toHaveBeenCalledTimes(1);
    expect(onLocateResume).not.toHaveBeenCalled();
  });

  it.each(["userId", "services", "chapter"] as const)(
    "hides a ready resume panel on the first %s-scope layout and does not revive it after A→B→A",
    async (changedScope) => {
      const fixture = editableFixture(1, "当前用户记录");
      const snapshot = { ...fixture.snapshot, resume_frame_id: "editable-frame" };
      const getNotes = vi.fn().mockResolvedValue(snapshot);
      const onLocateResume = vi.fn();
      const saveResume = vi.fn();
      projectionMocks.capture.mockResolvedValue(fixture.captured);
      projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

      const initial = panelProps({
        ...servicesFor(getNotes),
        savePersonalProductionResume: saveResume,
      }, {
        chapter: fixture.chapter,
        assets: fixture.assets,
        onLocateResume,
      });
      const commits: Array<{ panel: boolean; selector: boolean; locator: boolean }> = [];
      const renderObserved = (props: PanelProps) => (
        <>
          <PersonalProductionNotesPanel {...props} />
          <PanelCommitObserver onCommit={() => commits.push({
            panel: screen.queryByRole("region", { name: "我的制作记录" }) !== null,
            selector: screen.queryByRole("combobox", { name: "选择续作镜头" }) !== null,
            locator: screen.queryByRole("button", { name: "定位到续作镜头" }) !== null,
          })} />
        </>
      );
      const view = render(renderObserved(initial));
      const locate = await screen.findByRole("button", { name: "定位到续作镜头" });
      await userEvent.setup().click(locate);
      const target = onLocateResume.mock.calls[0]?.[0] as { isCurrent(): boolean } | undefined;
      expect(target?.isCurrent()).toBe(true);
      expect(getNotes).toHaveBeenCalledTimes(1);

      const beforeChange = commits.length;
      const changed: PanelProps = changedScope === "userId"
        ? { ...initial, userId: "another-user" }
        : changedScope === "services"
          ? { ...initial, services: { ...initial.services } }
          : { ...initial, chapter: { ...fixture.chapter } };
      view.rerender(renderObserved(changed));
      expect(commits[beforeChange]).toEqual({ panel: false, selector: false, locator: false });
      expect(target?.isCurrent()).toBe(false);
      expect(getNotes).toHaveBeenCalledTimes(1);

      const beforeReturn = commits.length;
      view.rerender(renderObserved(initial));
      expect(commits[beforeReturn]).toEqual({ panel: false, selector: false, locator: false });
      expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
      expect(getNotes).toHaveBeenCalledTimes(1);

      view.rerender(
        <PersonalProductionNotesPanel
          key="explicit-reopen"
          {...initial}
          contextToken={{}}
          generation={initial.generation + 1}
        />,
      );
      expect(await screen.findByRole("region", { name: "我的制作记录" })).toBeInTheDocument();
      expect(getNotes).toHaveBeenCalledTimes(2);
      expect(onLocateResume).toHaveBeenCalledTimes(1);
    },
  );

  it.each(["success", "401"] as const)(
    "ignores a deferred old %s read after opening a new user scope",
    async (outcome) => {
      const oldFixture = editableFixture(1, "旧用户迟到记录");
      const currentFixture = editableFixture(4, "新用户当前记录");
      const oldRequest = deferred<PersonalProductionSnapshot>();
      const getNotes = vi.fn()
        .mockImplementationOnce(() => oldRequest.promise)
        .mockResolvedValueOnce(currentFixture.snapshot);
      const unauthorized = vi.fn();
      projectionMocks.capture.mockResolvedValue(currentFixture.captured);
      projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

      const initial = panelProps({
        ...servicesFor(getNotes),
        savePersonalProductionResume: vi.fn(),
      }, {
        chapter: oldFixture.chapter,
        assets: oldFixture.assets,
        onUnauthorized: unauthorized,
      });
      const view = render(<PersonalProductionNotesPanel {...initial} />);
      await waitFor(() => expect(getNotes).toHaveBeenCalledTimes(1));

      const nextScope: PanelProps = { ...initial, userId: "new-user" };
      view.rerender(<PersonalProductionNotesPanel {...nextScope} />);
      expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
      expect(getNotes).toHaveBeenCalledTimes(1);

      view.rerender(
        <PersonalProductionNotesPanel
          key="new-user-panel"
          {...nextScope}
          contextToken={{}}
          generation={initial.generation + 1}
        />,
      );
      expect(await screen.findByText("新用户当前记录")).toBeInTheDocument();
      expect(getNotes).toHaveBeenCalledTimes(2);

      await act(async () => {
        if (outcome === "success") {
          oldRequest.resolve(oldFixture.snapshot);
          await oldRequest.promise;
        } else {
          oldRequest.reject(new ApiError("http", "late unauthorized", 401, "旧请求登录过期。"));
          await oldRequest.promise.catch(() => undefined);
        }
        await Promise.resolve();
      });

      expect(screen.getByText("新用户当前记录")).toBeInTheDocument();
      expect(screen.queryByText("旧用户迟到记录")).not.toBeInTheDocument();
      expect(unauthorized).not.toHaveBeenCalled();
      expect(getNotes).toHaveBeenCalledTimes(2);
    },
  );

  it("rejects R1 resume and note handlers after R2/R3 rereads and a close/reopen", async () => {
    const user = userEvent.setup();
    const fixture = editableFixture(3, "服务端备注");
    const snapshot = { ...fixture.snapshot, resume_frame_id: "retired-frame" };
    const getNotes = vi.fn().mockResolvedValue(snapshot);
    const saveNote = vi.fn(async (_chapterId: string, _update: PersonalProductionNoteUpdate) => snapshot);
    const saveResume = vi.fn(async (_chapterId: string, update: PersonalProductionResumeUpdate) => (
      updateResumeSnapshot(snapshot, update)
    ));
    const onClose = vi.fn();
    const onFrameReadInvalidated = vi.fn();
    projectionMocks.capture.mockResolvedValue(fixture.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    const initial = panelProps({
      ...servicesFor(getNotes),
      savePersonalProductionNote: saveNote,
      savePersonalProductionResume: saveResume,
    }, {
      chapter: fixture.chapter,
      assets: fixture.assets,
      onClose,
      onFrameReadInvalidated,
    });
    const view = render(<PersonalProductionNotesPanel {...initial} />);
    await screen.findByText("服务端备注");

    const resumeSelect = screen.getByRole("combobox", { name: "选择续作镜头" });
    await user.selectOptions(resumeSelect, "editable-frame");
    const oldSelect = latestEditorHandler("选择续作镜头");
    const oldResumeSave = latestEditorHandler("保存续作位置");
    const oldResumeClear = latestEditorHandler("清除续作位置");

    await user.click(screen.getByRole("button", { name: "编辑镜头 1 的个人记录" }));
    const oldNoteInput = latestEditorHandler("镜头 1 的文字备注");
    const oldNoteStatus = latestEditorHandler("镜头 1 的制作状态");
    await user.clear(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }));
    await user.type(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }), "R1旧草稿");
    await user.selectOptions(screen.getByRole("combobox", { name: "镜头 1 的制作状态" }), "unmarked");
    const oldNoteSave = latestEditorHandler("保存镜头记录");
    const oldNoteCancel = latestEditorHandler("关闭编辑器");
    const oldPanelClose = latestEditorHandler("关闭记录");

    async function openRereadResumeSelection(expectedReads: number): Promise<void> {
      await user.click(screen.getByRole("button", { name: "重新读取记录" }));
      await waitFor(() => expect(getNotes).toHaveBeenCalledTimes(expectedReads));
      await screen.findByText("服务端备注");
      await user.selectOptions(screen.getByRole("combobox", { name: "选择续作镜头" }), "editable-frame");
      expect(screen.getByRole("button", { name: "保存续作位置" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "清除续作位置" })).toBeEnabled();
    }

    async function editCurrentNote(draft: string): Promise<void> {
      await user.click(screen.getByRole("button", { name: "编辑镜头 1 的个人记录" }));
      const input = screen.getByRole("textbox", { name: "镜头 1 的文字备注" });
      await user.clear(input);
      await user.type(input, draft);
      expect(input).toHaveValue(draft);
    }

    async function replayR1ResumeHandlers(expectedReads: number): Promise<void> {
      const resumeSelection = screen.getByRole("combobox", { name: "选择续作镜头" });
      const invalidatedCount = onFrameReadInvalidated.mock.calls.length;
      const closeCount = onClose.mock.calls.length;

      function expectR1ResumeCallbacksWereRejected(): void {
        expect(resumeSelection).toHaveValue("editable-frame");
        expect(onFrameReadInvalidated).toHaveBeenCalledTimes(invalidatedCount);
        expect(onClose).toHaveBeenCalledTimes(closeCount);
        expect(getNotes).toHaveBeenCalledTimes(expectedReads);
        expect(saveResume).not.toHaveBeenCalled();
      }

      expectR1ResumeCallbacksWereRejected();
      expect(screen.getByRole("button", { name: "保存续作位置" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "清除续作位置" })).toBeEnabled();

      await act(async () => {
        oldSelect(valueChangeEvent(""));
        await Promise.resolve();
      });
      expectR1ResumeCallbacksWereRejected();

      await act(async () => {
        oldResumeSave(clickEvent());
        await Promise.resolve();
      });
      expectR1ResumeCallbacksWereRejected();

      await act(async () => {
        oldResumeClear(clickEvent());
        await Promise.resolve();
      });
      expectR1ResumeCallbacksWereRejected();
      expect(screen.getByRole("button", { name: "保存续作位置" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "清除续作位置" })).toBeEnabled();
    }

    async function replayR1NoteHandlers(expectedDraft: string, expectedReads: number): Promise<void> {
      const input = screen.getByRole("textbox", { name: "镜头 1 的文字备注" });
      const invalidatedCount = onFrameReadInvalidated.mock.calls.length;
      const closeCount = onClose.mock.calls.length;
      await act(async () => {
        oldNoteInput(valueChangeEvent("R1过期输入"));
        oldNoteStatus(valueChangeEvent("approved"));
        oldNoteSave(clickEvent());
        oldNoteCancel(clickEvent());
        oldPanelClose(clickEvent());
        await Promise.resolve();
      });

      expect(screen.getByRole("region", { name: "我的制作记录" })).toBeInTheDocument();
      expect(screen.getByRole("combobox", { name: "选择续作镜头" })).toHaveValue("editable-frame");
      expect(screen.getByRole("textbox", { name: "镜头 1 的文字备注" })).toHaveValue(expectedDraft);
      expect(screen.getByRole("combobox", { name: "镜头 1 的制作状态" })).toHaveValue("needs_revision");
      expect(onFrameReadInvalidated).toHaveBeenCalledTimes(invalidatedCount);
      expect(onClose).toHaveBeenCalledTimes(closeCount);
      expect(getNotes).toHaveBeenCalledTimes(expectedReads);
      expect(saveNote).not.toHaveBeenCalled();
      expect(saveResume).not.toHaveBeenCalled();
      expect(input).toBeInTheDocument();
    }

    await openRereadResumeSelection(2);
    await replayR1ResumeHandlers(2);
    await editCurrentNote("R2当前草稿");
    await replayR1NoteHandlers("R2当前草稿", 2);

    await openRereadResumeSelection(3);
    await replayR1ResumeHandlers(3);
    await editCurrentNote("R3当前草稿");
    await replayR1NoteHandlers("R3当前草稿", 3);

    await user.click(screen.getByRole("button", { name: "关闭记录" }));
    expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
    expect(onClose).toHaveBeenCalledTimes(1);

    view.rerender(
      <PersonalProductionNotesPanel
        key="new-owner-after-close"
        {...initial}
        contextToken={{}}
        generation={initial.generation + 1}
    />,
  );
    await screen.findByText("服务端备注");
    await user.selectOptions(screen.getByRole("combobox", { name: "选择续作镜头" }), "editable-frame");
    await replayR1ResumeHandlers(4);
    await editCurrentNote("重开后的草稿");

    await replayR1NoteHandlers("重开后的草稿", 4);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("ignores a digest that settles after a new resume panel has loaded", async () => {
    const oldFixture = editableFixture(1, "旧摘要读取");
    const currentFixture = editableFixture(2, "当前摘要读取");
    const oldDigest = deferred<CapturedPersonalProductionMedia>();
    const getNotes = vi.fn()
      .mockResolvedValueOnce(oldFixture.snapshot)
      .mockResolvedValueOnce(currentFixture.snapshot);
    projectionMocks.capture
      .mockImplementationOnce(() => oldDigest.promise)
      .mockResolvedValueOnce(currentFixture.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    const initial = panelProps({
      ...servicesFor(getNotes),
      savePersonalProductionResume: vi.fn(),
    }, {
      chapter: oldFixture.chapter,
      assets: oldFixture.assets,
    });
    const view = render(<PersonalProductionNotesPanel {...initial} />);
    await waitFor(() => expect(projectionMocks.capture).toHaveBeenCalledTimes(1));

    const nextScope: PanelProps = { ...initial, services: { ...initial.services } };
    view.rerender(<PersonalProductionNotesPanel {...nextScope} />);
    expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(1);

    view.rerender(
      <PersonalProductionNotesPanel
        key="new-digest-panel"
        {...nextScope}
        contextToken={{}}
        generation={initial.generation + 1}
      />,
    );
    expect(await screen.findByText("当前摘要读取")).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(2);
    expect(projectionMocks.capture).toHaveBeenCalledTimes(2);

    await act(async () => {
      oldDigest.resolve(oldFixture.captured);
      await oldDigest.promise;
      await Promise.resolve();
    });

    expect(screen.getByText("当前摘要读取")).toBeInTheDocument();
    expect(screen.queryByText("旧摘要读取")).not.toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(2);
    expect(projectionMocks.project).toHaveBeenCalledTimes(1);
  });

  it.each(["success", "401"] as const)(
    "does not let a late resume PUT %s replace or log out a new user scope",
    async (outcome) => {
      const user = userEvent.setup();
      const oldFixture = editableFixture(1, "旧用户记录");
      const currentFixture = editableFixture(5, "新用户记录");
      const pendingSave = deferred<PersonalProductionSnapshot>();
      const getNotes = vi.fn()
        .mockResolvedValueOnce(oldFixture.snapshot)
        .mockResolvedValueOnce(currentFixture.snapshot);
      const saveResume = vi.fn((_chapterId: string, _update: PersonalProductionResumeUpdate) => pendingSave.promise);
      const unauthorized = vi.fn();
      projectionMocks.capture.mockResolvedValue(currentFixture.captured);
      projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

      const initial = panelProps({
        ...servicesFor(getNotes),
        savePersonalProductionResume: saveResume,
      }, {
        chapter: oldFixture.chapter,
        assets: oldFixture.assets,
        onUnauthorized: unauthorized,
      });
      const view = render(<PersonalProductionNotesPanel {...initial} />);
      await screen.findByText("旧用户记录");
      await user.selectOptions(screen.getByRole("combobox", { name: "选择续作镜头" }), "editable-frame");
      await user.click(screen.getByRole("button", { name: "保存续作位置" }));
      await waitFor(() => expect(saveResume).toHaveBeenCalledTimes(1));
      expect(screen.getByText("正在保存续作位置…")).toBeInTheDocument();

      const nextScope: PanelProps = { ...initial, userId: "new-user" };
      view.rerender(<PersonalProductionNotesPanel {...nextScope} />);
      expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
      view.rerender(
        <PersonalProductionNotesPanel
          key="new-user-after-put"
          {...nextScope}
          contextToken={{}}
          generation={initial.generation + 1}
        />,
      );
      expect(await screen.findByText("新用户记录")).toBeInTheDocument();
      expect(getNotes).toHaveBeenCalledTimes(2);

      const update = saveResume.mock.calls[0]?.[1];
      if (update === undefined) {
        throw new Error("The pending resume write should have captured its update.");
      }
      await act(async () => {
        if (outcome === "success") {
          pendingSave.resolve(updateResumeSnapshot(oldFixture.snapshot, update));
          await pendingSave.promise;
        } else {
          pendingSave.reject(new ApiError("http", "late unauthorized", 401, "旧请求登录过期。"));
          await pendingSave.promise.catch(() => undefined);
        }
        await Promise.resolve();
      });

      expect(screen.getByText("新用户记录")).toBeInTheDocument();
      expect(screen.queryByText("旧用户记录")).not.toBeInTheDocument();
      expect(unauthorized).not.toHaveBeenCalled();
      expect(saveResume).toHaveBeenCalledTimes(1);
      expect(getNotes).toHaveBeenCalledTimes(2);
    },
  );

  it("keeps the current resume write gate locked during parent invalidation callbacks", async () => {
    const fixture = editableFixture(1, "已有个人记录");
    const getNotes = vi.fn().mockResolvedValue(fixture.snapshot);
    const pendingSave = deferred<PersonalProductionSnapshot>();
    const saveResume = vi.fn(() => pendingSave.promise);
    let reenterSave: (() => void) | null = null;
    const onFrameReadInvalidated = vi.fn(() => reenterSave?.());
    projectionMocks.capture.mockResolvedValue(fixture.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    render(
      <PersonalProductionNotesPanel
        {...panelProps({
          ...servicesFor(getNotes),
          savePersonalProductionResume: saveResume,
        }, {
          chapter: fixture.chapter,
          assets: fixture.assets,
          onFrameReadInvalidated,
        })}
      />,
    );

    await screen.findByText("已有个人记录");
    await userEvent.setup().selectOptions(screen.getByRole("combobox", { name: "选择续作镜头" }), "editable-frame");
    const saveHandler = latestEditorHandler("保存续作位置");
    reenterSave = () => saveHandler(new MouseEvent("click", { bubbles: true }));

    await act(async () => {
      saveHandler(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(onFrameReadInvalidated).toHaveBeenCalledTimes(1);
    expect(saveResume).toHaveBeenCalledTimes(1);
    expect(screen.getByText("正在保存续作位置…")).toBeInTheDocument();
    expect(screen.getByText("已有个人记录")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存续作位置" })).toBeDisabled();

    await act(async () => {
      pendingSave.resolve(updateResumeSnapshot(fixture.snapshot, {
        expected_revision: fixture.snapshot.revision,
        resume_frame_id: "editable-frame",
      }));
      await pendingSave.promise;
      await Promise.resolve();
    });

    expect(await screen.findByText("续作位置：镜头 1。页面不会自动跳转。")).toBeInTheDocument();
    expect(saveResume).toHaveBeenCalledTimes(1);
    expect(getNotes).toHaveBeenCalledTimes(1);
  });

  it("keeps an unchanged note draft eligible but blocks resume writes for a dirty retained note", async () => {
    const user = userEvent.setup();
    const fixture = editableFixture(1, "服务端备注");
    const getNotes = vi.fn().mockResolvedValue(fixture.snapshot);
    const saveNote = vi.fn(async (_chapterId: string, _update: PersonalProductionNoteUpdate) => fixture.snapshot);
    const saveResume = vi.fn();
    projectionMocks.capture.mockResolvedValue(fixture.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    render(
      <PersonalProductionNotesPanel
        {...panelProps({
          ...servicesFor(getNotes),
          savePersonalProductionNote: saveNote,
          savePersonalProductionResume: saveResume,
        }, {
          chapter: fixture.chapter,
          assets: fixture.assets,
        })}
      />,
    );

    await screen.findByText("服务端备注");
    await user.selectOptions(screen.getByRole("combobox", { name: "选择续作镜头" }), "editable-frame");
    await user.click(screen.getByRole("button", { name: "编辑镜头 1 的个人记录" }));
    await user.click(screen.getByRole("button", { name: "关闭编辑器" }));

    expect(screen.getByRole("button", { name: "保存续作位置" })).toBeEnabled();
    expect(saveResume).not.toHaveBeenCalled();
    expect(getNotes).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "编辑镜头 1 的个人记录" }));
    await user.clear(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }));
    await user.type(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }), "未保存修改");
    await user.click(screen.getByRole("button", { name: "关闭编辑器" }));

    expect(screen.getByText("请先保存备注，或重新读取以放弃未保存的个人记录修改，再修改续作位置。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存续作位置" })).toBeDisabled();
    expect(saveResume).not.toHaveBeenCalled();
    expect(getNotes).toHaveBeenCalledTimes(1);
  });

  it("rejects a captured resume save handler while a note write owns the shared gate", async () => {
    const user = userEvent.setup();
    const fixture = editableFixture(1, "服务端备注");
    const getNotes = vi.fn().mockResolvedValue(fixture.snapshot);
    const pendingNoteSave = deferred<PersonalProductionSnapshot>();
    const saveNote = vi.fn((_chapterId: string, _update: PersonalProductionNoteUpdate) => pendingNoteSave.promise);
    const saveResume = vi.fn();
    projectionMocks.capture.mockResolvedValue(fixture.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    render(
      <PersonalProductionNotesPanel
        {...panelProps({
          ...servicesFor(getNotes),
          savePersonalProductionNote: saveNote,
          savePersonalProductionResume: saveResume,
        }, {
          chapter: fixture.chapter,
          assets: fixture.assets,
        })}
      />,
    );

    await screen.findByText("服务端备注");
    await user.selectOptions(screen.getByRole("combobox", { name: "选择续作镜头" }), "editable-frame");
    const oldResumeSave = latestEditorHandler("保存续作位置");
    await user.click(screen.getByRole("button", { name: "编辑镜头 1 的个人记录" }));
    await user.clear(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }));
    await user.type(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }), "新的备注草稿");
    await user.selectOptions(screen.getByRole("combobox", { name: "镜头 1 的制作状态" }), "unmarked");
    const noteSave = latestEditorHandler("保存镜头记录");

    await act(async () => {
      noteSave(new MouseEvent("click", { bubbles: true }));
      oldResumeSave(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(saveNote).toHaveBeenCalledTimes(1);
    expect(saveResume).not.toHaveBeenCalled();
    expect(screen.getByText("正在保存镜头 1 的个人记录…")).toBeInTheDocument();

    const update = saveNote.mock.calls[0]?.[1];
    if (update === undefined) {
      throw new Error("The note save should have captured its update.");
    }
    await act(async () => {
      pendingNoteSave.resolve(updateEditableSnapshot(fixture.snapshot, update));
      await pendingNoteSave.promise;
      await Promise.resolve();
    });

    expect(await screen.findByText("新的备注草稿")).toBeInTheDocument();
    expect(saveNote).toHaveBeenCalledTimes(1);
    expect(saveResume).not.toHaveBeenCalled();
    expect(getNotes).toHaveBeenCalledTimes(1);
  });

  it("shows a saved resume as unlocatable when its frame is uniquely mapped but unverified", async () => {
    const fixture = editableFixture(1, "既有备注");
    const snapshotWithResume = {
      ...fixture.snapshot,
      resume_frame_id: "editable-frame",
    };
    const getNotes = vi.fn().mockResolvedValue(snapshotWithResume);
    projectionMocks.capture.mockResolvedValue(fixture.captured);
    projectionMocks.project.mockReturnValue(readout("既有备注", {
      mediaState: "ready",
      verified: false,
      resumePosition: null,
      resumeIsInvalid: true,
    }));

    render(
      <PersonalProductionNotesPanel
        {...panelProps(servicesFor(getNotes), {
          chapter: fixture.chapter,
          assets: fixture.assets,
        })}
      />,
    );

    expect(await screen.findByText("续作位置：镜头 1。页面不会自动跳转。")).toBeInTheDocument();
    expect(screen.getByText("续作位置已保存，但当前无法定位。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "定位到续作镜头" })).not.toBeInTheDocument();
  });

  it("can clear a saved resume when the canonical media snapshot is unreadable", async () => {
    const user = userEvent.setup();
    const currentChapter = chapter("chapter-unreadable-resume");
    const unreadableSnapshot = parsePersonalProductionSnapshot({
      chapter_id: currentChapter.id,
      revision: 4,
      media_state: "unreadable",
      frames: [],
      frame_notes: {},
      resume_frame_id: "removed-frame",
    }, currentChapter.id);
    const getNotes = vi.fn().mockResolvedValue(unreadableSnapshot);
    const saveResume = vi.fn(async (_chapterId: string, update: PersonalProductionResumeUpdate) => ({
      ...unreadableSnapshot,
      revision: unreadableSnapshot.revision + 1,
      resume_frame_id: update.resume_frame_id,
    }));
    projectionMocks.capture.mockResolvedValue({ state: "unreadable", frames: [] });
    projectionMocks.project.mockReturnValue(readout("媒体不可读", {
      mediaState: "unreadable",
      resumeIsInvalid: true,
    }));

    render(
      <PersonalProductionNotesPanel
        {...panelProps({
          ...servicesFor(getNotes),
          savePersonalProductionResume: saveResume,
        }, {
          chapter: currentChapter,
          assets: [],
          mediaSnapshotAvailable: false,
        })}
      />,
    );

    await screen.findByText("续作位置无法与当前章节对应。");
    const clearButton = screen.getByRole("button", { name: "清除续作位置" });
    expect(clearButton).toBeEnabled();
    await user.click(clearButton);

    await waitFor(() => expect(saveResume).toHaveBeenCalledTimes(1));
    expect(saveResume.mock.calls[0]?.[1]).toEqual({
      expected_revision: 4,
      resume_frame_id: null,
    });
    expect(getNotes).toHaveBeenCalledTimes(1);
  });

  it("retains a rejected resume intent until an explicit reread and a new choice", async () => {
    const user = userEvent.setup();
    const initial = editableFixture(1, "现有个人记录");
    const rereadSnapshot = {
      ...initial.snapshot,
      revision: 2,
    };
    const getNotes = vi.fn()
      .mockResolvedValueOnce(initial.snapshot)
      .mockResolvedValueOnce(rereadSnapshot);
    const saveResume = vi.fn()
      .mockRejectedValueOnce(new ApiError("http", "revision conflict", 409, "个人记录版本已变化。"))
      .mockImplementationOnce(async (_chapterId: string, update: PersonalProductionResumeUpdate) => (
        updateResumeSnapshot(rereadSnapshot, update)
      ));
    projectionMocks.capture.mockResolvedValue(initial.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    render(
      <PersonalProductionNotesPanel
        {...panelProps({
          ...servicesFor(getNotes),
          savePersonalProductionResume: saveResume,
        }, {
          chapter: initial.chapter,
          assets: initial.assets,
        })}
      />,
    );

    await screen.findByText("现有个人记录");
    await user.selectOptions(screen.getByRole("combobox", { name: "选择续作镜头" }), "editable-frame");
    await user.click(screen.getByRole("button", { name: "保存续作位置" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("续作位置保存未完成");
    expect(screen.getByRole("alert")).toHaveTextContent("操作意图已保留为只读");
    expect(screen.getByRole("button", { name: "重新读取并核实" })).toBeInTheDocument();
    expect(screen.getByText("现有个人记录")).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(1);
    expect(saveResume).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "重新读取并核实" }));
    await waitFor(() => expect(getNotes).toHaveBeenCalledTimes(2));
    expect(saveResume).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("combobox", { name: "选择续作镜头" })).toHaveValue("");

    await user.selectOptions(screen.getByRole("combobox", { name: "选择续作镜头" }), "editable-frame");
    await user.click(screen.getByRole("button", { name: "保存续作位置" }));
    expect(await screen.findByText("续作位置：镜头 1。页面不会自动跳转。")).toBeInTheDocument();
    expect(saveResume).toHaveBeenCalledTimes(2);
    expect(saveResume.mock.calls[1]?.[1]).toEqual({
      expected_revision: 2,
      resume_frame_id: "editable-frame",
    });
    expect(getNotes).toHaveBeenCalledTimes(2);
  });

  it("keeps a 409 resume gate through a failed explicit read and unlocks only after recovery", async () => {
    const user = userEvent.setup();
    const fixture = editableFixture(2, "应保留的记录");
    const refreshed = { ...fixture.snapshot, revision: 3 };
    const getNotes = vi.fn()
      .mockResolvedValueOnce(fixture.snapshot)
      .mockRejectedValueOnce(new ApiError("http", "read failed", 503, "暂时无法读取。"))
      .mockResolvedValueOnce(refreshed);
    const saveResume = vi.fn().mockRejectedValue(
      new ApiError("http", "revision conflict", 409, "个人记录版本已变化。"),
    );
    projectionMocks.capture.mockResolvedValue(fixture.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    render(
      <PersonalProductionNotesPanel
        {...panelProps({ ...servicesFor(getNotes), savePersonalProductionResume: saveResume }, {
          chapter: fixture.chapter,
          assets: fixture.assets,
        })}
      />,
    );

    await screen.findByText("应保留的记录");
    await user.selectOptions(screen.getByRole("combobox", { name: "选择续作镜头" }), "editable-frame");
    await user.click(screen.getByRole("button", { name: "保存续作位置" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("续作位置保存未完成");

    await user.click(screen.getByRole("button", { name: "重新读取并核实" }));
    await waitFor(() => expect(getNotes).toHaveBeenCalledTimes(2));
    await waitFor(() => {
      expect(screen.getAllByRole("alert").some((alert) => (
        alert.textContent?.includes("本地服务暂时不可用") === true
      ))).toBe(true);
    });
    expect(screen.getByText("续作位置保存未完成")).toBeInTheDocument();
    expect(screen.getByText("应保留的记录")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新读取并核实" })).toBeEnabled();
    expect(screen.getByRole("combobox", { name: "选择续作镜头" })).toBeDisabled();
    expect(saveResume).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "重新读取并核实" }));
    await waitFor(() => expect(getNotes).toHaveBeenCalledTimes(3));
    expect(await screen.findByText("应保留的记录")).toBeInTheDocument();
    expect(screen.queryByText("续作位置保存未完成")).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "选择续作镜头" })).toHaveValue("");
    expect(saveResume).toHaveBeenCalledTimes(1);
  });

  it("locks an unknown resume result synchronously and recovers only after an explicit read", async () => {
    const user = userEvent.setup();
    const fixture = editableFixture(2, "未知结果时保留的记录");
    const getNotes = vi.fn()
      .mockResolvedValueOnce(fixture.snapshot)
      .mockResolvedValueOnce(fixture.snapshot);
    const saveResume = vi.fn().mockRejectedValue(
      new ApiError("network", "connection lost", null, null),
    );
    projectionMocks.capture.mockResolvedValue(fixture.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    render(
      <PersonalProductionNotesPanel
        {...panelProps({ ...servicesFor(getNotes), savePersonalProductionResume: saveResume }, {
          chapter: fixture.chapter,
          assets: fixture.assets,
        })}
      />,
    );

    await screen.findByText("未知结果时保留的记录");
    await user.selectOptions(screen.getByRole("combobox", { name: "选择续作镜头" }), "editable-frame");
    const save = latestEditorHandler("保存续作位置");
    await act(async () => {
      save(clickEvent());
      save(clickEvent());
      await Promise.resolve();
    });

    expect(saveResume).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("alert")).toHaveTextContent("无法确定续作位置是否已保存");
    expect(screen.getByText("未知结果时保留的记录")).toBeInTheDocument();
    await act(async () => {
      save(clickEvent());
      await Promise.resolve();
    });
    expect(saveResume).toHaveBeenCalledTimes(1);
    expect(getNotes).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "重新读取并核实" }));
    await waitFor(() => expect(getNotes).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("无法确定续作位置是否已保存")).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "选择续作镜头" })).toHaveValue("");
    expect(saveResume).toHaveBeenCalledTimes(1);
  });

  it("uses the current unauthorized flow for a resume save 401", async () => {
    const user = userEvent.setup();
    const fixture = editableFixture(1, "个人记录");
    const getNotes = vi.fn().mockResolvedValue(fixture.snapshot);
    const unauthorized = vi.fn();
    const saveResume = vi.fn().mockRejectedValue(
      new ApiError("http", "unauthorized", 401, "登录状态已失效。"),
    );
    projectionMocks.capture.mockResolvedValue(fixture.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    render(
      <PersonalProductionNotesPanel
        {...panelProps({
          ...servicesFor(getNotes),
          savePersonalProductionResume: saveResume,
        }, {
          chapter: fixture.chapter,
          assets: fixture.assets,
          onUnauthorized: unauthorized,
        })}
      />,
    );

    await screen.findByText("个人记录");
    await user.selectOptions(screen.getByRole("combobox", { name: "选择续作镜头" }), "editable-frame");
    await user.click(screen.getByRole("button", { name: "保存续作位置" }));

    await waitFor(() => expect(unauthorized).toHaveBeenCalledTimes(1));
    expect(saveResume).toHaveBeenCalledTimes(1);
    expect(getNotes).toHaveBeenCalledTimes(1);
  });

});
