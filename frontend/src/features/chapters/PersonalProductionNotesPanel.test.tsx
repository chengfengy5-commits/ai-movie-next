import { useLayoutEffect, useMemo, useState, type ComponentProps } from "react";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Chapter, StoryboardAsset } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import {
  parsePersonalProductionSnapshot,
  type PersonalProductionNoteUpdate,
  type PersonalProductionSnapshot,
} from "../../shared/api/personalProductionNotes";
import type { WorkspaceServices } from "../../shared/api/services";
import type { CapturedPersonalProductionMedia, PersonalProductionReadout } from "./personal-production/projection";
import {
  PersonalProductionNotesPanel,
  type PersonalProductionNoteFrameTarget,
  type PersonalProductionResumeTarget,
} from "./PersonalProductionNotesPanel";

const projectionMocks = vi.hoisted(() => ({ capture: vi.fn(), project: vi.fn() }));
const statusFilterRuntime = vi.hoisted(() => ({
  handlers: [] as Array<{ label: string; onClick: (event: MouseEvent) => void }>,
}));
const editorRuntime = vi.hoisted(() => {
  const handlers: Array<{ name: string; invoke: (event: unknown) => void }> = [];
  return {
    handlers,
    capture(_type: unknown, props: unknown) {
      if (typeof props !== "object" || props === null) {
        return;
      }
      const record = props as Record<string, unknown>;
      if (
        typeof record["aria-label"] === "string"
        && typeof record.onChange === "function"
      ) {
        handlers.push({
          name: record["aria-label"],
          invoke: record.onChange as (event: unknown) => void,
        });
        return;
      }
      if (typeof record.children === "string" && typeof record.onClick === "function") {
        const name = record.children.trim();
        if (name === "保存镜头记录" || name === "关闭编辑器" || name === "关闭记录") {
          handlers.push({
            name,
            invoke: record.onClick as (event: unknown) => void,
          });
        }
      }
    },
  };
});

vi.mock("react/jsx-runtime", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react/jsx-runtime")>();
  const capture = (type: unknown, props: unknown) => {
    editorRuntime.capture(type, props);
    if (type !== "button" || typeof props !== "object" || props === null) {
      return;
    }
    const record = props as Record<string, unknown>;
    if (
      typeof record["aria-pressed"] !== "boolean"
      || typeof record["aria-label"] !== "string"
      || typeof record.onClick !== "function"
    ) {
      return;
    }
    statusFilterRuntime.handlers.push({
      label: record["aria-label"],
      onClick: record.onClick as (event: MouseEvent) => void,
    });
  };
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
  const capture = (type: unknown, props: unknown) => {
    editorRuntime.capture(type, props);
    if (type !== "button" || typeof props !== "object" || props === null) {
      return;
    }
    const record = props as Record<string, unknown>;
    if (
      typeof record["aria-pressed"] !== "boolean"
      || typeof record["aria-label"] !== "string"
      || typeof record.onClick !== "function"
    ) {
      return;
    }
    statusFilterRuntime.handlers.push({
      label: record["aria-label"],
      onClick: record.onClick as (event: MouseEvent) => void,
    });
  };
  return {
    ...actual,
    jsxDEV: (...args: Parameters<typeof actual.jsxDEV>) => {
      capture(args[0], args[1]);
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

function snapshot(chapterId: string, note: string): PersonalProductionSnapshot {
  return parsePersonalProductionSnapshot({
    chapter_id: chapterId,
    revision: 1,
    media_state: "empty",
    frames: [],
    frame_notes: { orphan: { status: "needs_revision", note } },
    resume_frame_id: null,
  }, chapterId);
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
  return {
    revision: snapshotValue.revision,
    noSavedRecord: snapshotValue.revision === 0,
    mediaState: "ready",
    frames: [{ position: 1, status, note, hasSavedNote, verified: true }],
    orphanNotes: [],
    resumePosition: null,
    resumeIsInvalid: false,
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

function readoutFrame(
  position: number,
  status: PersonalProductionReadout["frames"][number]["status"],
  note: string,
  verified = status !== "unverifiable" && status !== "unreadable",
): PersonalProductionReadout["frames"][number] {
  return { position, status, note, hasSavedNote: true, verified };
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

function PanelHarness({
  services,
  currentChapter,
  onUnauthorized,
  onLocateResume,
  onResumeInvalidated,
}: {
  services: WorkspaceServices;
  currentChapter: Chapter;
  onUnauthorized: () => void;
  onLocateResume: (target: PersonalProductionResumeTarget) => void;
  onResumeInvalidated: () => void;
}) {
  const [open, setOpen] = useState(true);
  const [generation, setGeneration] = useState(0);
  const [contextToken, setContextToken] = useState<object>(() => ({}));
  const assets = useMemo(() => [], []);
  const assetSnapshotToken = useMemo(() => ({}), []);

  return (
    <>
      {!open && (
        <button
          onClick={() => {
            setContextToken({});
            setGeneration((current) => current + 1);
            setOpen(true);
          }}
          type="button"
        >
          打开记录
        </button>
      )}
      {open && (
        <PersonalProductionNotesPanel
          contextToken={contextToken}
          assetSnapshotToken={assetSnapshotToken}
          generation={generation}
          assets={assets}
          chapter={currentChapter}
          mediaSnapshotAvailable
          onClose={() => setOpen(false)}
          onRetryChapter={vi.fn()}
          onUnauthorized={onUnauthorized}
          onLocateResume={onLocateResume}
          onResumeInvalidated={onResumeInvalidated}
          onRegisterFrameRead={vi.fn()}
          onFrameReadInvalidated={vi.fn()}
          onLocateProductionNoteFrame={vi.fn(() => true)}
          services={services}
          userId="current-user"
          seriesId="series-panel"
        />
      )}
    </>
  );
}

function PanelCommitObserver({ onCommit }: { onCommit: () => void }) {
  useLayoutEffect(() => {
    onCommit();
  });
  return null;
}

describe("personal production notes panel", () => {
  beforeEach(() => {
    projectionMocks.capture.mockReset();
    projectionMocks.project.mockReset();
    statusFilterRuntime.handlers = [];
    editorRuntime.handlers.length = 0;
    projectionMocks.capture.mockResolvedValue({ state: "empty", frames: [] } satisfies CapturedPersonalProductionMedia);
    projectionMocks.project.mockImplementation((
      _chapter: Chapter,
      _captured: CapturedPersonalProductionMedia,
      value: PersonalProductionSnapshot,
    ) => readout(savedNoteText(value.frame_notes.get("orphan"))));
  });

  afterEach(() => cleanup());

  it("rejects native editor handlers from an older edit session", async () => {
    const user = userEvent.setup();
    const fixture = editableFixture(1, "原有备注");
    const getNotes = vi.fn().mockResolvedValue(fixture.snapshot);
    const save = vi.fn();
    projectionMocks.capture.mockResolvedValue(fixture.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    render(
      <PersonalProductionNotesPanel
        {...panelProps({ ...servicesFor(getNotes), savePersonalProductionNote: save }, {
          chapter: fixture.chapter,
          assets: fixture.assets,
        })}
      />,
    );

    await user.click(await screen.findByRole("button", { name: "编辑镜头 1 的个人记录" }));
    const oldNoteChange = latestEditorHandler("镜头 1 的文字备注");
    const oldStatusChange = latestEditorHandler("镜头 1 的制作状态");
    const oldSave = latestEditorHandler("保存镜头记录");
    const oldCancel = latestEditorHandler("关闭编辑器");

    await user.click(screen.getByRole("button", { name: "关闭编辑器" }));
    await user.click(screen.getByRole("button", { name: "编辑镜头 1 的个人记录" }));
    const currentNote = screen.getByRole("textbox", { name: "镜头 1 的文字备注" });
    await user.clear(currentNote);
    await user.type(currentNote, "R2 当前草稿");
    const currentStatus = screen.getByRole("combobox", { name: "镜头 1 的制作状态" });
    await user.selectOptions(currentStatus, "unmarked");

    const readsBeforeReplay = getNotes.mock.calls.length;
    await act(async () => {
      oldNoteChange({ currentTarget: { value: "R1 迟到文字" } });
      oldStatusChange({ currentTarget: { value: "approved" } });
      oldSave(new MouseEvent("click", { bubbles: true }));
      oldCancel(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(screen.getByRole("region", { name: "编辑镜头 1 的个人记录" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "镜头 1 的文字备注" })).toHaveValue("R2 当前草稿");
    expect(screen.getByRole("combobox", { name: "镜头 1 的制作状态" })).toHaveValue("unmarked");
    expect(save).not.toHaveBeenCalled();
    expect(getNotes).toHaveBeenCalledTimes(readsBeforeReplay);
  });

  it("rejects native editor handlers from an earlier read after two explicit rereads", async () => {
    const user = userEvent.setup();
    const fixture = editableFixture(1, "初始记录");
    const getNotes = vi.fn().mockResolvedValue(fixture.snapshot);
    const save = vi.fn();
    const register = vi.fn();
    const frameInvalidated = vi.fn();
    const resumeInvalidated = vi.fn();
    const close = vi.fn();
    projectionMocks.capture.mockResolvedValue(fixture.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    render(
      <PersonalProductionNotesPanel
        {...panelProps({ ...servicesFor(getNotes), savePersonalProductionNote: save }, {
          chapter: fixture.chapter,
          assets: fixture.assets,
          onRegisterFrameRead: register,
          onFrameReadInvalidated: frameInvalidated,
          onResumeInvalidated: resumeInvalidated,
          onClose: close,
        })}
      />,
    );

    await user.click(await screen.findByRole("button", { name: "编辑镜头 1 的个人记录" }));
    const r1PanelClose = latestEditorHandler("关闭记录");
    const r1NoteChange = latestEditorHandler("镜头 1 的文字备注");
    const r1StatusChange = latestEditorHandler("镜头 1 的制作状态");
    const r1Save = latestEditorHandler("保存镜头记录");
    const r1Cancel = latestEditorHandler("关闭编辑器");

    await user.click(screen.getByRole("button", { name: "重新读取记录" }));
    await user.click(await screen.findByRole("button", { name: "编辑镜头 1 的个人记录" }));
    await user.clear(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }));
    await user.type(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }), "R2 当前草稿");
    await user.selectOptions(screen.getByRole("combobox", { name: "镜头 1 的制作状态" }), "unmarked");

    await user.click(screen.getByRole("button", { name: "重新读取记录" }));
    await user.click(await screen.findByRole("button", { name: "编辑镜头 1 的个人记录" }));
    expect(screen.getByRole("textbox", { name: "镜头 1 的文字备注" })).toHaveValue("初始记录");
    expect(screen.getByRole("combobox", { name: "镜头 1 的制作状态" })).toHaveValue("needs_revision");
    await user.clear(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }));
    await user.type(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }), "R3 当前草稿");
    await user.selectOptions(screen.getByRole("combobox", { name: "镜头 1 的制作状态" }), "unmarked");

    const registrationsBeforeReplay = register.mock.calls.length;
    const invalidationsBeforeReplay = frameInvalidated.mock.calls.length;
    const resumeInvalidationsBeforeReplay = resumeInvalidated.mock.calls.length;
    const readsBeforeReplay = getNotes.mock.calls.length;
    await act(async () => {
      r1NoteChange({ currentTarget: { value: "R1 迟到文字" } });
      r1StatusChange({ currentTarget: { value: "approved" } });
      r1Save(new MouseEvent("click", { bubbles: true }));
      r1Cancel(new MouseEvent("click", { bubbles: true }));
      r1PanelClose(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(screen.getByRole("region", { name: "编辑镜头 1 的个人记录" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "镜头 1 的文字备注" })).toHaveValue("R3 当前草稿");
    expect(screen.getByRole("combobox", { name: "镜头 1 的制作状态" })).toHaveValue("unmarked");
    expect(getNotes).toHaveBeenCalledTimes(readsBeforeReplay);
    expect(register).toHaveBeenCalledTimes(registrationsBeforeReplay);
    expect(frameInvalidated).toHaveBeenCalledTimes(invalidationsBeforeReplay);
    expect(resumeInvalidated).toHaveBeenCalledTimes(resumeInvalidationsBeforeReplay);
    expect(close).not.toHaveBeenCalled();
    expect(save).not.toHaveBeenCalled();
  });

  it("uses the latest draft when a native save handler from the same edit session runs", async () => {
    const user = userEvent.setup();
    const fixture = editableFixture(1, "原有备注");
    const getNotes = vi.fn().mockResolvedValue(fixture.snapshot);
    const save = vi.fn(async (_chapterId: string, update: PersonalProductionNoteUpdate) => (
      updateEditableSnapshot(fixture.snapshot, update)
    ));
    projectionMocks.capture.mockResolvedValue(fixture.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    render(
      <PersonalProductionNotesPanel
        {...panelProps({ ...servicesFor(getNotes), savePersonalProductionNote: save }, {
          chapter: fixture.chapter,
          assets: fixture.assets,
        })}
      />,
    );

    await user.click(await screen.findByRole("button", { name: "编辑镜头 1 的个人记录" }));
    const saveHandler = latestEditorHandler("保存镜头记录");
    await user.selectOptions(screen.getByRole("combobox", { name: "镜头 1 的制作状态" }), "unmarked");
    await user.clear(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }));
    await user.type(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }), "当前最新草稿");

    await act(async () => {
      saveHandler(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(await screen.findByText("当前最新草稿")).toBeInTheDocument();
    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0]?.[1].frames[0]?.note).toBe("当前最新草稿");
    expect(save.mock.calls[0]?.[1].frames[0]?.status).toBe("unmarked");
  });

  it("creates the first revision-zero record and publishes the validated response without another GET", async () => {
    const user = userEvent.setup();
    const fixture = editableFixture();
    const getNotes = vi.fn().mockResolvedValue(fixture.snapshot);
    const save = vi.fn(async (_chapterId: string, update: PersonalProductionNoteUpdate) => (
      updateEditableSnapshot(fixture.snapshot, update)
    ));
    const register = vi.fn();
    projectionMocks.capture.mockResolvedValue(fixture.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    render(
      <PersonalProductionNotesPanel
        {...panelProps({
          ...servicesFor(getNotes),
          savePersonalProductionNote: save,
        }, {
          chapter: fixture.chapter,
          assets: fixture.assets,
          onRegisterFrameRead: register,
        })}
      />,
    );

    expect(await screen.findByText("你还没有保存个人制作记录。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "新增镜头 1 的个人记录" }));
    expect(screen.getByRole("heading", { name: "编辑镜头 1 的个人记录" })).toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "镜头 1 的制作状态" }), "needs_revision");
    await user.type(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }), "首次保存的文字");
    await user.click(screen.getByRole("button", { name: "保存镜头记录" }));

    expect(await screen.findByText("首次保存的文字")).toBeInTheDocument();
    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0]?.[0]).toBe(fixture.chapter.id);
    expect(save.mock.calls[0]?.[1]).toEqual({
      expected_revision: 0,
      frames: [{
        storyboard_asset_id: "editable-frame",
        expected_media_revision: 1,
        status: "needs_revision",
        note: "首次保存的文字",
      }],
    });
    expect(getNotes).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("你还没有保存个人制作记录。")).not.toBeInTheDocument();
    const registrations = register.mock.calls.map(([identity]) => identity.requestGeneration);
    expect(registrations.at(-1)).toBeGreaterThan(registrations.at(-2) ?? -1);
    expect(screen.getByRole("button", { name: "编辑镜头 1 的个人记录" })).toBeInTheDocument();
  });

  it("keeps a conflict draft until an explicit reread and a new status choice", async () => {
    const user = userEvent.setup();
    const initial = editableFixture(2, "服务端旧备注");
    const reread = editableFixture(3, "另一会话已保存");
    const getNotes = vi.fn()
      .mockResolvedValueOnce(initial.snapshot)
      .mockResolvedValueOnce(reread.snapshot);
    const save = vi.fn()
      .mockRejectedValueOnce(new ApiError("http", "conflict", 409, "个人记录版本已变化。"))
      .mockImplementationOnce(async (_chapterId: string, update: PersonalProductionNoteUpdate) => (
        updateEditableSnapshot(reread.snapshot, update)
      ));
    projectionMocks.capture.mockResolvedValue(initial.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    render(
      <PersonalProductionNotesPanel
        {...panelProps({ ...servicesFor(getNotes), savePersonalProductionNote: save }, {
          chapter: initial.chapter,
          assets: initial.assets,
        })}
      />,
    );

    await user.click(await screen.findByRole("button", { name: "编辑镜头 1 的个人记录" }));
    const noteField = screen.getByRole("textbox", { name: "镜头 1 的文字备注" });
    await user.clear(noteField);
    await user.type(noteField, "保留在本地的草稿");
    await user.click(screen.getByRole("button", { name: "保存镜头记录" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("保存未完成");
    expect(screen.getByRole("alert")).toHaveTextContent("草稿仍保留");
    expect(save).toHaveBeenCalledTimes(1);
    expect(getNotes).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "重新读取并核实" }));

    expect(await screen.findByText("另一会话已保存")).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("button", { name: "编辑镜头 1 的个人记录" }));
    expect(screen.getByRole("textbox", { name: "镜头 1 的文字备注" })).toHaveValue("保留在本地的草稿");
    expect(screen.getByRole("combobox", { name: "镜头 1 的制作状态" })).toHaveValue("");
    expect(screen.getByRole("button", { name: "保存镜头记录" })).toBeDisabled();

    await user.selectOptions(screen.getByRole("combobox", { name: "镜头 1 的制作状态" }), "needs_revision");
    await user.click(screen.getByRole("button", { name: "保存镜头记录" }));
    expect(await screen.findByText("保留在本地的草稿")).toBeInTheDocument();
    expect(save).toHaveBeenCalledTimes(2);
    expect(save.mock.calls[1]?.[1].expected_revision).toBe(3);
    expect(getNotes).toHaveBeenCalledTimes(2);
  });

  it("keeps a conflicted draft visible and closes with the current null read registration", async () => {
    const user = userEvent.setup();
    const initial = editableFixture(2, "服务端当前备注");
    const getNotes = vi.fn().mockResolvedValue(initial.snapshot);
    const save = vi.fn().mockRejectedValue(
      new ApiError("http", "conflict", 409, "个人记录版本已变化。"),
    );
    const register = vi.fn();
    const close = vi.fn();
    projectionMocks.capture.mockResolvedValue(initial.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    render(
      <PersonalProductionNotesPanel
        {...panelProps({ ...servicesFor(getNotes), savePersonalProductionNote: save }, {
          chapter: initial.chapter,
          assets: initial.assets,
          onRegisterFrameRead: register,
          onClose: close,
        })}
      />,
    );

    await user.click(await screen.findByRole("button", { name: "编辑镜头 1 的个人记录" }));
    const oldPanelClose = latestEditorHandler("关闭记录");
    const noteField = screen.getByRole("textbox", { name: "镜头 1 的文字备注" });
    await user.clear(noteField);
    await user.type(noteField, "冲突后仍可查看的草稿");
    await user.click(screen.getByRole("button", { name: "保存镜头记录" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("保存未完成");
    expect(screen.getByRole("alert")).toHaveTextContent("冲突后仍可查看的草稿");
    expect(screen.queryByText("正在读取个人制作记录…")).not.toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(1);

    const registrations = register.mock.calls.map(([identity]) => identity);
    const previousReadyIdentity = [...registrations].reverse().find((identity) => identity.readout !== null);
    const currentIdentity = registrations.at(-1);
    expect(previousReadyIdentity).toBeDefined();
    expect(currentIdentity).toEqual({
      readout: null,
      requestGeneration: expect.any(Number),
    });
    expect(currentIdentity?.requestGeneration).toBeGreaterThan(previousReadyIdentity?.requestGeneration ?? -1);

    await act(async () => {
      oldPanelClose(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    expect(screen.getByRole("alert")).toHaveTextContent("冲突后仍可查看的草稿");
    expect(close).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "关闭记录" }));
    expect(close).toHaveBeenCalledTimes(1);
    expect(close.mock.calls[0]?.[0]).toBe(currentIdentity);
  });

  it("keeps a failed draft read-only when explicit verification finds an invalid target", async () => {
    const user = userEvent.setup();
    const initial = editableFixture(2, "服务端当前备注");
    const invalidSnapshot: PersonalProductionSnapshot = {
      ...initial.snapshot,
      revision: 3,
      frames: initial.snapshot.frames.map((frame) => ({ ...frame, source_valid: false })),
    };
    const getNotes = vi.fn()
      .mockResolvedValueOnce(initial.snapshot)
      .mockResolvedValueOnce(invalidSnapshot);
    const save = vi.fn().mockRejectedValue(
      new ApiError("http", "conflict", 409, "个人记录版本已变化。"),
    );
    projectionMocks.capture.mockResolvedValue(initial.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    render(
      <PersonalProductionNotesPanel
        {...panelProps({ ...servicesFor(getNotes), savePersonalProductionNote: save }, {
          chapter: initial.chapter,
          assets: initial.assets,
        })}
      />,
    );

    await user.click(await screen.findByRole("button", { name: "编辑镜头 1 的个人记录" }));
    await user.clear(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }));
    await user.type(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }), "目标失效后保留的草稿");
    await user.click(screen.getByRole("button", { name: "保存镜头记录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("目标失效后保留的草稿");

    await user.click(screen.getByRole("button", { name: "重新读取并核实" }));

    expect(await screen.findByText("服务端当前备注")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("目标失效后保留的草稿");
    expect(screen.getByRole("alert")).toHaveTextContent("请重新读取记录核实后再继续");
    expect(screen.queryByRole("textbox", { name: "镜头 1 的文字备注" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑镜头 1 的个人记录" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "保存镜头记录" })).not.toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(2);
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("locks an uncertain save until explicit verification and status confirmation", async () => {
    const user = userEvent.setup();
    const initial = editableFixture(2, "服务端当前备注");
    const invalidReply: PersonalProductionSnapshot = {
      ...initial.snapshot,
      chapter_id: "different-chapter",
      revision: initial.snapshot.revision + 1,
    };
    const getNotes = vi.fn().mockResolvedValue(initial.snapshot);
    const save = vi.fn().mockResolvedValue(invalidReply);
    projectionMocks.capture.mockResolvedValue(initial.captured);
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

    render(
      <PersonalProductionNotesPanel
        {...panelProps({ ...servicesFor(getNotes), savePersonalProductionNote: save }, {
          chapter: initial.chapter,
          assets: initial.assets,
        })}
      />,
    );

    await user.click(await screen.findByRole("button", { name: "编辑镜头 1 的个人记录" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "镜头 1 的制作状态" }), "unmarked");
    await user.clear(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }));
    await user.type(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }), "未知结果保留的草稿");
    const oldSave = latestEditorHandler("保存镜头记录");
    const oldCancel = latestEditorHandler("关闭编辑器");
    await act(async () => {
      oldSave(new MouseEvent("click", { bubbles: true }));
      oldSave(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("无法确定是否已保存");
    expect(screen.getByRole("alert")).toHaveTextContent("未知结果保留的草稿");
    expect(screen.getByRole("alert")).toHaveTextContent("状态：未标记");
    expect(save).toHaveBeenCalledTimes(1);
    expect(getNotes).toHaveBeenCalledTimes(1);

    await act(async () => {
      oldSave(new MouseEvent("click", { bubbles: true }));
      oldSave(new MouseEvent("click", { bubbles: true }));
      oldCancel(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    expect(save).toHaveBeenCalledTimes(1);
    expect(getNotes).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert")).toHaveTextContent("未知结果保留的草稿");

    await user.click(screen.getByRole("button", { name: "重新读取并核实" }));
    expect(await screen.findByText("服务端当前备注")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "编辑镜头 1 的个人记录" }));
    expect(screen.getByRole("textbox", { name: "镜头 1 的文字备注" })).toHaveValue("未知结果保留的草稿");
    expect(screen.getByRole("combobox", { name: "镜头 1 的制作状态" })).toHaveValue("");
    expect(screen.getByRole("button", { name: "保存镜头记录" })).toBeDisabled();
    expect(getNotes).toHaveBeenCalledTimes(2);
    expect(save).toHaveBeenCalledTimes(1);
  });

  it.each(["late success", "late 401"] as const)(
    "does not let an old save %s change a newly opened account panel",
    async (settlement) => {
      const user = userEvent.setup();
      const oldFixture = editableFixture(2, "A账号草稿前记录");
      const newFixture = editableFixture(4, "B账号当前记录");
      const pendingSave = deferred<PersonalProductionSnapshot>();
      const oldSave = vi.fn((_chapterId: string, _update: PersonalProductionNoteUpdate) => pendingSave.promise);
      const oldGet = vi.fn().mockResolvedValue(oldFixture.snapshot);
      const newGet = vi.fn().mockResolvedValue(newFixture.snapshot);
      const newUnauthorized = vi.fn();
      projectionMocks.capture.mockResolvedValue(oldFixture.captured);
      projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));
      const oldServices = { ...servicesFor(oldGet), savePersonalProductionNote: oldSave };
      const newServices = {
        ...servicesFor(newGet),
        savePersonalProductionNote: vi.fn(async (_chapterId: string, update: PersonalProductionNoteUpdate) => (
          updateEditableSnapshot(newFixture.snapshot, update)
        )),
      };
      const props = panelProps(oldServices, {
        chapter: oldFixture.chapter,
        assets: oldFixture.assets,
        onClose: vi.fn(),
      });
      const view = render(<PersonalProductionNotesPanel {...props} />);

      await user.click(await screen.findByRole("button", { name: "编辑镜头 1 的个人记录" }));
      await user.click(screen.getByRole("button", { name: "保存镜头记录" }));
      expect(await screen.findByText("正在保存镜头 1 的个人记录…")).toBeInTheDocument();
      expect(screen.getByText("正在保存的内容 · 镜头 1")).toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent("文字备注：A账号草稿前记录");
      expect(oldSave).toHaveBeenCalledTimes(1);
      await user.click(screen.getByRole("button", { name: "关闭记录" }));
      view.unmount();

      render(
        <PersonalProductionNotesPanel
          {...panelProps(newServices, {
            chapter: newFixture.chapter,
            assets: newFixture.assets,
            userId: "user-b",
            contextToken: {},
            generation: 2,
            onUnauthorized: newUnauthorized,
          })}
        />,
      );
      expect(await screen.findByText("B账号当前记录")).toBeInTheDocument();

      await act(async () => {
        if (settlement === "late success") {
          const update = oldSave.mock.calls[0]?.[1];
          if (update === undefined) {
            throw new Error("The old save should have captured its original update.");
          }
          pendingSave.resolve(updateEditableSnapshot(oldFixture.snapshot, update));
          await pendingSave.promise;
        } else {
          pendingSave.reject(new ApiError("http", "unauthorized", 401));
          await pendingSave.promise.catch(() => undefined);
        }
        await Promise.resolve();
      });

      expect(screen.getByText("B账号当前记录")).toBeInTheDocument();
      expect(screen.queryByText("保留的旧账号草稿")).not.toBeInTheDocument();
      expect(newUnauthorized).not.toHaveBeenCalled();
      expect(newGet).toHaveBeenCalledTimes(1);
    },
  );

  it("filters only current rows, keeps full counts and safety sections, and locates the original row", async () => {
    const user = userEvent.setup();
    const currentChapter: Chapter = {
      ...chapter(),
      content: [
        { text: "已认可镜头", storyboard: ["shot-filter-1"] },
        { text: "待修镜头", storyboard: ["shot-filter-2"] },
        { text: "待重确认镜头", storyboard: ["shot-filter-3"] },
        { text: "无法核对镜头", storyboard: ["shot-filter-4"] },
      ],
    };
    const currentAssets = [
      storyboardAsset("shot-filter-1", currentChapter.id),
      storyboardAsset("shot-filter-2", currentChapter.id),
      storyboardAsset("shot-filter-3", currentChapter.id),
      storyboardAsset("shot-filter-4", currentChapter.id),
    ];
    const getNotes = vi.fn().mockResolvedValue(snapshot(currentChapter.id, "保留显示的旧记录"));
    const captured: CapturedPersonalProductionMedia = { state: "ready", frames: [] };
    projectionMocks.capture.mockResolvedValue(captured);
    projectionMocks.project.mockReturnValue(readout("保留显示的旧记录", {
      mediaState: "ready",
      resumePosition: 1,
      frames: [
        readoutFrame(1, "approved", "已认可内容"),
        readoutFrame(2, "needs_revision", "待修内容"),
        readoutFrame(3, "needs_reconfirmation", "待重确认内容"),
        readoutFrame(4, "unverifiable", "当前无法核对内容", false),
      ],
    }));
    const register = vi.fn();
    const invalidated = vi.fn();
    const locateResume = vi.fn();
    const locateFrame = vi.fn((_target: PersonalProductionNoteFrameTarget) => true);

    render(
      <PersonalProductionNotesPanel
        {...panelProps(servicesFor(getNotes), {
          chapter: currentChapter,
          assets: currentAssets,
          onRegisterFrameRead: register,
          onFrameReadInvalidated: invalidated,
          onLocateResume: locateResume,
          onLocateProductionNoteFrame: locateFrame,
        })}
      />,
    );

    const group = await screen.findByRole("group", { name: "按状态筛选" });
    expect(within(group).getByRole("button", { name: "全部 4" })).toHaveAttribute("aria-pressed", "true");
    expect(within(group).getByRole("button", { name: "待修 1" })).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "已认可 1" })).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "未标记 0" })).toBeInTheDocument();
    expect(screen.getByText("保留显示的旧记录")).toBeInTheDocument();
    expect(screen.getByText("续作位置：镜头 1。页面不会自动跳转。")).toBeInTheDocument();
    expect(screen.getByText("部分镜头与当前媒体快照无法核对，不会显示为当前已认可。")).toBeInTheDocument();

    const calls = {
      getNotes: getNotes.mock.calls.length,
      capture: projectionMocks.capture.mock.calls.length,
      register: register.mock.calls.length,
      invalidated: invalidated.mock.calls.length,
    };
    await user.click(within(group).getByRole("button", { name: "待修 1" }));
    expect(within(group).getByRole("button", { name: "待修 1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("待修内容")).toBeInTheDocument();
    expect(screen.getByText("镜头 2")).toBeInTheDocument();
    expect(screen.queryByText("已认可内容")).not.toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "全部 4" })).toBeInTheDocument();
    expect(screen.getByText("保留显示的旧记录")).toBeInTheDocument();
    expect(screen.getByText("续作位置：镜头 1。页面不会自动跳转。")).toBeInTheDocument();
    expect(screen.getByText("部分镜头与当前媒体快照无法核对，不会显示为当前已认可。")).toBeInTheDocument();

    await user.click(within(group).getByRole("button", { name: "记录不可识别 0" }));
    expect(screen.getByText("这份当前镜头记录中没有“记录不可识别”状态。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看全部记录" })).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "当前镜头制作记录" })).not.toBeInTheDocument();
    expect(screen.getByText("保留显示的旧记录")).toBeInTheDocument();
    expect(screen.getByText("续作位置：镜头 1。页面不会自动跳转。")).toBeInTheDocument();
    expect(screen.getByText("部分镜头与当前媒体快照无法核对，不会显示为当前已认可。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看全部记录" }));
    expect(screen.getByRole("list", { name: "当前镜头制作记录" })).toBeInTheDocument();

    await user.click(within(group).getByRole("button", { name: "待修 1" }));
    await user.click(screen.getByRole("button", { name: "定位到记录镜头 2" }));
    expect(locateFrame).toHaveBeenCalledTimes(1);
    expect(locateFrame.mock.calls[0]?.[0].row).toBe(
      (projectionMocks.project.mock.results[0]?.value as PersonalProductionReadout).frames[1],
    );
    expect(getNotes).toHaveBeenCalledTimes(calls.getNotes);
    expect(projectionMocks.capture).toHaveBeenCalledTimes(calls.capture);
    expect(register).toHaveBeenCalledTimes(calls.register);
    expect(invalidated).toHaveBeenCalledTimes(calls.invalidated);
    expect(locateResume).not.toHaveBeenCalled();
  });

  it("rejects a real stale filter button callback after R2 becomes the current read", async () => {
    const user = userEvent.setup();
    const currentChapter: Chapter = {
      ...chapter(),
      content: [
        { text: "R1待修镜头", storyboard: ["shot-filter-r1"] },
        { text: "R2已认可镜头", storyboard: ["shot-filter-r2"] },
      ],
    };
    const getNotes = vi.fn()
      .mockResolvedValueOnce(snapshot(currentChapter.id, "R1摘要"))
      .mockResolvedValueOnce(snapshot(currentChapter.id, "R2摘要"))
      .mockResolvedValueOnce(snapshot(currentChapter.id, "R3摘要"));
    projectionMocks.capture.mockResolvedValue({ state: "ready", frames: [] });
    projectionMocks.project.mockImplementation((_chapter, _captured, value) => {
      const note = savedNoteText(value.frame_notes.get("orphan"));
      return readout(note, {
        mediaState: "ready",
        frames: note === "R1摘要"
          ? [readoutFrame(1, "approved", "R1已认可"), readoutFrame(2, "needs_revision", "R1待修")]
          : [readoutFrame(1, "approved", "R2已认可")],
      });
    });
    const register = vi.fn();
    const invalidated = vi.fn();
    const unauthorized = vi.fn();
    const view = render(
      <PersonalProductionNotesPanel
        {...panelProps(servicesFor(getNotes), {
          chapter: currentChapter,
          assets: [storyboardAsset("shot-filter-r1", currentChapter.id), storyboardAsset("shot-filter-r2", currentChapter.id)],
          onRegisterFrameRead: register,
          onFrameReadInvalidated: invalidated,
          onUnauthorized: unauthorized,
        })}
      />,
    );

    const group = await screen.findByRole("group", { name: "按状态筛选" });
    await user.click(within(group).getByRole("button", { name: "待修 1" }));
    expect(screen.getByText("R1待修")).toBeInTheDocument();
    const oldHandler = statusFilterRuntime.handlers
      .filter((entry) => entry.label === "待修 1")
      .at(-1)?.onClick;
    if (oldHandler === undefined) {
      throw new Error("The original status button callback should be observed from the JSX runtime.");
    }

    await user.click(screen.getByRole("button", { name: "重新读取记录" }));
    expect(await screen.findByText("R2摘要")).toBeInTheDocument();
    const currentGroup = screen.getByRole("group", { name: "按状态筛选" });
    expect(within(currentGroup).getByRole("button", { name: "全部 1" })).toHaveAttribute("aria-pressed", "true");
    expect(within(currentGroup).getByRole("button", { name: "待修 0" })).toHaveAttribute("aria-pressed", "false");
    const r2NeedsRevision = within(currentGroup).getByRole("button", { name: "待修 0" });
    await user.click(r2NeedsRevision);
    expect(r2NeedsRevision).toHaveAttribute("aria-pressed", "true");
    expect(within(currentGroup).getByRole("button", { name: "全部 1" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("这份当前镜头记录中没有“待修”状态。")).toBeInTheDocument();
    expect(screen.queryByText("R2已认可")).not.toBeInTheDocument();
    expect(screen.queryByText("R1待修")).not.toBeInTheDocument();
    const settledCalls = {
      getNotes: getNotes.mock.calls.length,
      capture: projectionMocks.capture.mock.calls.length,
      register: register.mock.calls.length,
      invalidated: invalidated.mock.calls.length,
    };

    await act(async () => {
      oldHandler(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(within(currentGroup).getByRole("button", { name: "待修 0" })).toHaveAttribute("aria-pressed", "true");
    expect(within(currentGroup).getByRole("button", { name: "全部 1" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("这份当前镜头记录中没有“待修”状态。")).toBeInTheDocument();
    expect(screen.queryByText("R2已认可")).not.toBeInTheDocument();
    expect(screen.queryByText("R1待修")).not.toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(settledCalls.getNotes);
    expect(projectionMocks.capture).toHaveBeenCalledTimes(settledCalls.capture);
    expect(register).toHaveBeenCalledTimes(settledCalls.register);
    expect(invalidated).toHaveBeenCalledTimes(settledCalls.invalidated);
    expect(unauthorized).not.toHaveBeenCalled();

    view.unmount();
    render(
      <PersonalProductionNotesPanel
        {...panelProps(servicesFor(getNotes), {
          contextToken: {},
          generation: 2,
          chapter: currentChapter,
          assets: [storyboardAsset("shot-filter-r1", currentChapter.id), storyboardAsset("shot-filter-r2", currentChapter.id)],
          onRegisterFrameRead: register,
          onFrameReadInvalidated: invalidated,
          onUnauthorized: unauthorized,
        })}
      />,
    );
    expect(await screen.findByText("R3摘要")).toBeInTheDocument();
    const reopenedGroup = screen.getByRole("group", { name: "按状态筛选" });
    const reopenedNeedsRevision = within(reopenedGroup).getByRole("button", { name: "待修 0" });
    await user.click(reopenedNeedsRevision);
    expect(reopenedNeedsRevision).toHaveAttribute("aria-pressed", "true");
    const reopenedCalls = {
      getNotes: getNotes.mock.calls.length,
      capture: projectionMocks.capture.mock.calls.length,
      register: register.mock.calls.length,
      invalidated: invalidated.mock.calls.length,
    };

    await act(async () => {
      oldHandler(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(within(reopenedGroup).getByRole("button", { name: "待修 0" })).toHaveAttribute("aria-pressed", "true");
    expect(within(reopenedGroup).getByRole("button", { name: "全部 1" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("这份当前镜头记录中没有“待修”状态。")).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(reopenedCalls.getNotes);
    expect(projectionMocks.capture).toHaveBeenCalledTimes(reopenedCalls.capture);
    expect(register).toHaveBeenCalledTimes(reopenedCalls.register);
    expect(invalidated).toHaveBeenCalledTimes(reopenedCalls.invalidated);
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it("allows explicit locating of a verified resume even when it needs reconfirmation", async () => {
    const user = userEvent.setup();
    const onLocateResume = vi.fn();
    const getNotes = vi.fn().mockResolvedValue(snapshot("chapter-panel", "待重新确认的记录"));
    projectionMocks.capture.mockResolvedValue({ state: "ready", frames: [] } satisfies CapturedPersonalProductionMedia);
    projectionMocks.project.mockReturnValue(readout("待重新确认的记录", {
      mediaState: "ready",
      resumePosition: 1,
      status: "needs_reconfirmation",
    }));

    render(<PersonalProductionNotesPanel {...panelProps(servicesFor(getNotes), { onLocateResume })} />);

    const locateButton = await screen.findByRole("button", { name: "定位到续作镜头" });
    expect(onLocateResume).not.toHaveBeenCalled();
    expect(getNotes).toHaveBeenCalledTimes(1);
    await user.click(locateButton);

    expect(onLocateResume).toHaveBeenCalledTimes(1);
    const target = onLocateResume.mock.calls[0]?.[0] as PersonalProductionResumeTarget | undefined;
    expect(target?.position).toBe(1);
    expect(target?.isCurrent()).toBe(true);
    expect(screen.getByRole("button", { name: "定位到续作镜头" })).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(1);
  });

  it.each(["services", "chapter"] as const)(
    "hides the old ready locator on the first %s-scope commit and stays closed across A→B→A",
    async (changedScope) => {
      const user = userEvent.setup();
      const getNotes = vi.fn().mockResolvedValue(snapshot("chapter-panel", "当前范围的续作记录"));
      projectionMocks.capture.mockResolvedValue({ state: "ready", frames: [] } satisfies CapturedPersonalProductionMedia);
      projectionMocks.project.mockReturnValue(readout("当前范围的续作记录", {
        mediaState: "ready",
        resumePosition: 1,
      }));

      const initial = panelProps(servicesFor(getNotes));
      const observedCommits: Array<{ regionVisible: boolean; locatorVisible: boolean }> = [];
      const observeCommit = () => {
        observedCommits.push({
          regionVisible: screen.queryByRole("region", { name: "我的制作记录" }) !== null,
          locatorVisible: screen.queryByRole("button", { name: "定位到续作镜头" }) !== null,
        });
      };
      const renderObserved = (props: PanelProps) => (
        <>
          <PersonalProductionNotesPanel {...props} />
          <PanelCommitObserver onCommit={observeCommit} />
        </>
      );
      const view = render(renderObserved(initial));

      await user.click(await screen.findByRole("button", { name: "定位到续作镜头" }));
      expect(screen.getByRole("region", { name: "我的制作记录" })).toBeInTheDocument();
      const target = (initial.onLocateResume as ReturnType<typeof vi.fn>).mock.calls[0]?.[0] as
        PersonalProductionResumeTarget | undefined;
      expect(target?.isCurrent()).toBe(true);

      const commitsBeforeChange = observedCommits.length;
      const changed = changedScope === "services"
        ? { ...initial, services: servicesFor(getNotes) }
        : { ...initial, chapter: { ...initial.chapter } };
      view.rerender(renderObserved(changed));

      const firstChangedCommit = observedCommits[commitsBeforeChange];
      expect(firstChangedCommit).toEqual({ regionVisible: false, locatorVisible: false });
      expect(target?.isCurrent()).toBe(false);
      expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
      expect(getNotes).toHaveBeenCalledTimes(1);

      const commitsBeforeReturn = observedCommits.length;
      view.rerender(renderObserved(initial));
      expect(observedCommits[commitsBeforeReturn]).toEqual({ regionVisible: false, locatorVisible: false });
      expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "定位到续作镜头" })).not.toBeInTheDocument();
      expect(getNotes).toHaveBeenCalledTimes(1);
    },
  );

  it.each(["userId", "services", "chapter"] as const)(
    "hides a ready row locator on the first %s-scope commit and does not revive it after A→B→A",
    async (changedScope) => {
      const currentChapter: Chapter = {
        ...chapter(),
        content: [{ text: "可定位的当前镜头", storyboard: ["shot-note-1"] }],
      };
      const getNotes = vi.fn().mockResolvedValue(snapshot(currentChapter.id, "当前逐镜头记录"));
      projectionMocks.capture.mockResolvedValue({ state: "ready", frames: [] } satisfies CapturedPersonalProductionMedia);
      projectionMocks.project.mockReturnValue(readout("待重新确认", {
        mediaState: "ready",
        status: "needs_reconfirmation",
        verified: true,
      }));
      const locateFrame = vi.fn((_target: PersonalProductionNoteFrameTarget) => true);
      const initial = panelProps(servicesFor(getNotes), {
        chapter: currentChapter,
        assets: [storyboardAsset("shot-note-1", currentChapter.id)],
        onLocateProductionNoteFrame: locateFrame,
      });
      const observedCommits: Array<{
        regionVisible: boolean;
        rowLocatorVisible: boolean;
        filterVisible: boolean;
      }> = [];
      const observeCommit = () => {
        observedCommits.push({
          regionVisible: screen.queryByRole("region", { name: "我的制作记录" }) !== null,
          rowLocatorVisible: screen.queryByRole("button", { name: "定位到记录镜头 1" }) !== null,
          filterVisible: screen.queryByRole("group", { name: "按状态筛选" }) !== null,
        });
      };
      const renderObserved = (props: PanelProps) => (
        <>
          <PersonalProductionNotesPanel {...props} />
          <PanelCommitObserver onCommit={observeCommit} />
        </>
      );
      const view = render(renderObserved(initial));
      const user = userEvent.setup();
      const reconfirmationFilter = await screen.findByRole("button", { name: "待重新确认 1" });
      await user.click(reconfirmationFilter);
      expect(reconfirmationFilter).toHaveAttribute("aria-pressed", "true");
      await user.click(await screen.findByRole("button", { name: "定位到记录镜头 1" }));
      const target = locateFrame.mock.calls[0]?.[0];
      expect(target?.isCurrent()).toBe(true);
      expect(getNotes).toHaveBeenCalledTimes(1);

      const commitCountBeforeChange = observedCommits.length;
      const changed: PanelProps = changedScope === "userId"
        ? { ...initial, userId: "another-user" }
        : changedScope === "services"
          ? { ...initial, services: servicesFor(getNotes) }
          : { ...initial, chapter: { ...currentChapter } };
      view.rerender(renderObserved(changed));
      expect(observedCommits[commitCountBeforeChange]).toEqual({
        regionVisible: false,
        rowLocatorVisible: false,
        filterVisible: false,
      });
      expect(target?.isCurrent()).toBe(false);
      expect(getNotes).toHaveBeenCalledTimes(1);

      const commitCountBeforeReturn = observedCommits.length;
      view.rerender(renderObserved(initial));
      expect(observedCommits[commitCountBeforeReturn]).toEqual({
        regionVisible: false,
        rowLocatorVisible: false,
        filterVisible: false,
      });
      expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
      expect(getNotes).toHaveBeenCalledTimes(1);

      view.unmount();
      render(
        <PersonalProductionNotesPanel
          {...initial}
          contextToken={{}}
          generation={initial.generation + 1}
        />,
      );
      expect(await screen.findByRole("button", { name: "定位到记录镜头 1" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "全部 1" })).toHaveAttribute("aria-pressed", "true");
      expect(getNotes).toHaveBeenCalledTimes(2);
    },
  );

  it.each(["userId", "services", "chapter"] as const)(
    "hides an editable draft on the first %s-scope commit and keeps it invalid across A→B→A",
    async (changedScope) => {
      const user = userEvent.setup();
      const fixture = editableFixture(1, "服务端当前备注");
      const getNotes = vi.fn().mockResolvedValue(fixture.snapshot);
      const save = vi.fn();
      projectionMocks.capture.mockResolvedValue(fixture.captured);
      projectionMocks.project.mockImplementation((_chapter, _captured, value) => projectEditableSnapshot(value));

      const initial = panelProps({ ...servicesFor(getNotes), savePersonalProductionNote: save }, {
        chapter: fixture.chapter,
        assets: fixture.assets,
      });
      const observedCommits: Array<{ panelVisible: boolean; editorVisible: boolean }> = [];
      const observeCommit = () => {
        observedCommits.push({
          panelVisible: screen.queryByRole("region", { name: "我的制作记录" }) !== null,
          editorVisible: screen.queryByRole("textbox", { name: "镜头 1 的文字备注" }) !== null,
        });
      };
      const renderObserved = (props: PanelProps) => (
        <>
          <PersonalProductionNotesPanel {...props} />
          <PanelCommitObserver onCommit={observeCommit} />
        </>
      );
      const view = render(renderObserved(initial));

      await user.click(await screen.findByRole("button", { name: "编辑镜头 1 的个人记录" }));
      await user.clear(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }));
      await user.type(screen.getByRole("textbox", { name: "镜头 1 的文字备注" }), "旧范围私有草稿");
      const oldInput = latestEditorHandler("镜头 1 的文字备注");
      expect(screen.getByRole("textbox", { name: "镜头 1 的文字备注" })).toHaveValue("旧范围私有草稿");

      const changed: PanelProps = changedScope === "userId"
        ? { ...initial, userId: "another-user" }
        : changedScope === "services"
          ? { ...initial, services: servicesFor(getNotes) }
          : { ...initial, chapter: { ...fixture.chapter } };
      const commitsBeforeChange = observedCommits.length;
      view.rerender(renderObserved(changed));
      expect(observedCommits[commitsBeforeChange]).toEqual({ panelVisible: false, editorVisible: false });
      expect(getNotes).toHaveBeenCalledTimes(1);

      const commitsBeforeReturn = observedCommits.length;
      view.rerender(renderObserved(initial));
      expect(observedCommits[commitsBeforeReturn]).toEqual({ panelVisible: false, editorVisible: false });
      expect(getNotes).toHaveBeenCalledTimes(1);

      await act(async () => {
        oldInput({ currentTarget: { value: "迟到的旧范围文字" } });
        await Promise.resolve();
      });
      expect(screen.queryByRole("textbox", { name: "镜头 1 的文字备注" })).not.toBeInTheDocument();
      expect(save).not.toHaveBeenCalled();
      expect(getNotes).toHaveBeenCalledTimes(1);

      view.unmount();
      render(
        <PersonalProductionNotesPanel
          {...initial}
          contextToken={{}}
          generation={initial.generation + 1}
        />,
      );
      await user.click(await screen.findByRole("button", { name: "编辑镜头 1 的个人记录" }));
      expect(screen.getByRole("textbox", { name: "镜头 1 的文字备注" })).toHaveValue("服务端当前备注");
      expect(getNotes).toHaveBeenCalledTimes(2);
    },
  );

  it("ignores a digest that settles after closing and reopening the same chapter", async () => {
    const user = userEvent.setup();
    const oldDigest = deferred<CapturedPersonalProductionMedia>();
    const getNotes = vi.fn()
      .mockResolvedValueOnce(snapshot("chapter-panel", "旧面板迟到的摘要"))
      .mockResolvedValueOnce(snapshot("chapter-panel", "当前面板记录"));
    projectionMocks.capture
      .mockReturnValueOnce(oldDigest.promise)
      .mockResolvedValueOnce({ state: "empty", frames: [] } satisfies CapturedPersonalProductionMedia);
    const unauthorized = vi.fn();
    const onLocateResume = vi.fn();
    const onResumeInvalidated = vi.fn();

    render(
      <PanelHarness
        services={servicesFor(getNotes)}
        currentChapter={chapter()}
        onUnauthorized={unauthorized}
        onLocateResume={onLocateResume}
        onResumeInvalidated={onResumeInvalidated}
      />,
    );
    await waitFor(() => expect(projectionMocks.capture).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("正在本机核对已加载的媒体信息…")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "关闭记录" }));
    await user.click(screen.getByRole("button", { name: "打开记录" }));
    expect(await screen.findByText("当前面板记录")).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(2);

    await act(async () => {
      oldDigest.resolve({ state: "empty", frames: [] });
      await oldDigest.promise;
      await Promise.resolve();
    });
    expect(screen.queryByText("旧面板迟到的摘要")).not.toBeInTheDocument();
    expect(screen.getByText("当前面板记录")).toBeInTheDocument();
    expect(projectionMocks.project).toHaveBeenCalledTimes(1);
    expect(unauthorized).not.toHaveBeenCalled();
    expect(onLocateResume).not.toHaveBeenCalled();
    expect(onResumeInvalidated).toHaveBeenCalled();
  });

  it.each(["loading", "checking", "error"] as const)(
    "closes the notes panel safely while the read is %s",
    async (phase) => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      const unauthorized = vi.fn();
      const onResumeInvalidated = vi.fn();
      const lateSnapshot = deferred<PersonalProductionSnapshot>();
      const lateMedia = deferred<CapturedPersonalProductionMedia>();
      const getNotes = phase === "loading"
        ? vi.fn(() => lateSnapshot.promise)
        : phase === "checking"
          ? vi.fn().mockResolvedValue(snapshot("chapter-panel", "核对中的记录"))
          : vi.fn().mockRejectedValue(new ApiError("http", "failed", 500));
      if (phase === "checking") {
        projectionMocks.capture.mockReturnValueOnce(lateMedia.promise);
      }
      const services = servicesFor(getNotes);

      render(
        <PersonalProductionNotesPanel
          {...panelProps(services, { onClose, onUnauthorized: unauthorized, onResumeInvalidated })}
        />,
      );
      if (phase === "loading") {
        expect(await screen.findByText("正在读取个人制作记录…")).toBeInTheDocument();
      } else if (phase === "checking") {
        expect(await screen.findByText("正在本机核对已加载的媒体信息…")).toBeInTheDocument();
      } else {
        expect(await screen.findByRole("alert")).toBeInTheDocument();
      }

      await user.click(screen.getByRole("button", { name: "关闭记录" }));
      expect(onClose).toHaveBeenCalledTimes(1);
      expect(onResumeInvalidated).toHaveBeenCalled();
      expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();

      if (phase === "loading") {
        await act(async () => {
          lateSnapshot.reject(new ApiError("http", "unauthorized", 401));
          await lateSnapshot.promise.catch(() => undefined);
        });
      } else if (phase === "checking") {
        await act(async () => {
          lateMedia.resolve({ state: "ready", frames: [] });
          await lateMedia.promise;
        });
      }
      expect(projectionMocks.project).not.toHaveBeenCalled();
      expect(unauthorized).not.toHaveBeenCalled();
      expect(screen.queryByText("核对中的记录")).not.toBeInTheDocument();
    },
  );

  it("invalidates the resume target before the close callback runs", async () => {
    const user = userEvent.setup();
    const targetHolder: { current: PersonalProductionResumeTarget | null } = { current: null };
    const onClose = vi.fn(() => expect(targetHolder.current?.isCurrent()).toBe(false));
    const onLocateResume = vi.fn((target: PersonalProductionResumeTarget) => {
      targetHolder.current = target;
    });
    const invalidated = vi.fn();
    const getNotes = vi.fn().mockResolvedValue(snapshot("chapter-panel", "可定位记录"));
    projectionMocks.capture.mockResolvedValue({ state: "ready", frames: [] } satisfies CapturedPersonalProductionMedia);
    projectionMocks.project.mockReturnValue(readout("可定位记录", {
      mediaState: "ready",
      resumePosition: 1,
    }));

    render(
      <PersonalProductionNotesPanel
        {...panelProps(servicesFor(getNotes), {
          onClose,
          onLocateResume,
          onResumeInvalidated: invalidated,
        })}
      />,
    );
    await user.click(await screen.findByRole("button", { name: "定位到续作镜头" }));
    expect(targetHolder.current?.isCurrent()).toBe(true);

    await user.click(screen.getByRole("button", { name: "关闭记录" }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(targetHolder.current?.isCurrent()).toBe(false);
    expect(invalidated).toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
  });

  it.each([
    {
      label: "missing resume position",
      value: readout("无位置", { mediaState: "ready", resumePosition: null }),
    },
    {
      label: "invalid resume identity",
      value: readout("无效身份", { mediaState: "ready", resumePosition: null, resumeIsInvalid: true }),
    },
    {
      label: "mismatched media snapshot",
      value: readout("摘要不匹配", { mediaState: "mismatch", resumePosition: null, resumeIsInvalid: true }),
    },
    {
      label: "digest verification failure",
      value: readout("摘要无法核对", { mediaState: "ready", resumePosition: null, resumeIsInvalid: true }),
      digestUnavailable: true,
    },
  ])("does not offer locating for $label", async ({ value, digestUnavailable }) => {
    const getNotes = vi.fn().mockResolvedValue(snapshot("chapter-panel", "记录"));
    if (digestUnavailable) {
      projectionMocks.capture.mockResolvedValue({
        state: "ready",
        frames: [{
          frameIndex: 0,
          candidateId: "shot-a",
          storyboardAssetId: "shot-a",
          identityValid: true,
          assetImageUrl: null,
          previewUrl: null,
          assetImageDigest: null,
          previewDigest: null,
          digestAvailable: false,
        }],
      } satisfies CapturedPersonalProductionMedia);
    } else {
      projectionMocks.capture.mockResolvedValue({ state: "ready", frames: [] } satisfies CapturedPersonalProductionMedia);
    }
    projectionMocks.project.mockReturnValue(value);

    render(<PersonalProductionNotesPanel {...panelProps(servicesFor(getNotes))} />);

    await waitFor(() => expect(projectionMocks.project).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("button", { name: "定位到续作镜头" })).not.toBeInTheDocument();
  });

  it("does not offer locating when the asset snapshot is unavailable", async () => {
    const getNotes = vi.fn().mockResolvedValue(snapshot("chapter-panel", "记录"));
    projectionMocks.capture.mockResolvedValue({ state: "ready", frames: [] } satisfies CapturedPersonalProductionMedia);
    projectionMocks.project.mockReturnValue(readout("记录", { mediaState: "ready", resumePosition: 1 }));

    render(
      <PersonalProductionNotesPanel
        {...panelProps(servicesFor(getNotes), { mediaSnapshotAvailable: false })}
      />,
    );

    await waitFor(() => expect(projectionMocks.project).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("button", { name: "定位到续作镜头" })).not.toBeInTheDocument();
  });

  it("offers row navigation only for a current verified row with an available media snapshot", async () => {
    const user = userEvent.setup();
    const currentChapter: Chapter = {
      ...chapter(),
      content: [{ text: "当前帧", storyboard: ["shot-note-1"] }],
    };
    const currentAssets = [storyboardAsset("shot-note-1", currentChapter.id)];
    const getNotes = vi.fn().mockResolvedValue(snapshot(currentChapter.id, "逐行记录"));
    const navigate = vi.fn((_target: PersonalProductionNoteFrameTarget) => true);
    projectionMocks.capture.mockResolvedValue({ state: "ready", frames: [] } satisfies CapturedPersonalProductionMedia);
    projectionMocks.project.mockReturnValue(readout("逐行记录", {
      mediaState: "ready",
      status: "needs_reconfirmation",
      verified: true,
    }));

    render(
      <PersonalProductionNotesPanel
        {...panelProps(servicesFor(getNotes), {
          chapter: currentChapter,
          assets: currentAssets,
          onLocateProductionNoteFrame: navigate,
        })}
      />,
    );

    const locateButton = await screen.findByRole("button", { name: "定位到记录镜头 1" });
    await user.click(locateButton);
    expect(navigate).toHaveBeenCalledTimes(1);
    const target = navigate.mock.calls[0]?.[0];
    expect(target?.position).toBe(1);
    expect(target?.storyboardAssetId).toBe("shot-note-1");
    expect(target?.scope.mediaSnapshotAvailable).toBe(true);
    expect(target?.isCurrent()).toBe(true);
    expect(getNotes).toHaveBeenCalledTimes(1);
  });

  it("does not expose an unreadable row even when the readout otherwise matches the current frame", async () => {
    const currentChapter: Chapter = {
      ...chapter(),
      content: [{ text: "当前帧", storyboard: ["shot-note-1"] }],
    };
    const getNotes = vi.fn().mockResolvedValue(snapshot(currentChapter.id, "损坏记录"));
    projectionMocks.capture.mockResolvedValue({ state: "ready", frames: [] } satisfies CapturedPersonalProductionMedia);
    projectionMocks.project.mockReturnValue(readout("损坏记录", {
      mediaState: "ready",
      status: "unreadable",
      verified: true,
    }));

    render(
      <PersonalProductionNotesPanel
        {...panelProps(servicesFor(getNotes), {
          chapter: currentChapter,
          assets: [storyboardAsset("shot-note-1", currentChapter.id)],
        })}
      />,
    );

    expect(await screen.findByText("这条记录无法识别，未计入当前状态。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "定位到记录镜头 1" })).not.toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(1);
  });

  it("does not offer row navigation without an available media snapshot", async () => {
    const currentChapter: Chapter = {
      ...chapter(),
      content: [{ text: "当前帧", storyboard: ["shot-note-1"] }],
    };
    const getNotes = vi.fn().mockResolvedValue(snapshot(currentChapter.id, "逐行记录"));
    projectionMocks.capture.mockResolvedValue({ state: "ready", frames: [] } satisfies CapturedPersonalProductionMedia);
    projectionMocks.project.mockReturnValue(readout("逐行记录", {
      mediaState: "ready",
      status: "needs_reconfirmation",
      verified: true,
    }));

    render(
      <PersonalProductionNotesPanel
        {...panelProps(servicesFor(getNotes), {
          chapter: currentChapter,
          assets: [storyboardAsset("shot-note-1", currentChapter.id)],
          mediaSnapshotAvailable: false,
        })}
      />,
    );

    expect((await screen.findAllByText("逐行记录")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "定位到记录镜头 1" })).not.toBeInTheDocument();
  });

  it("does not expose a locator while local media verification is still checking", async () => {
    const currentChapter: Chapter = {
      ...chapter(),
      content: [{ text: "当前帧", storyboard: ["shot-note-1"] }],
    };
    const getNotes = vi.fn().mockResolvedValue(snapshot(currentChapter.id, "核对中的记录"));
    const captured = deferred<CapturedPersonalProductionMedia>();
    projectionMocks.capture.mockReturnValueOnce(captured.promise);
    projectionMocks.project.mockReturnValue(readout("核对中的记录", {
      mediaState: "ready",
      status: "needs_reconfirmation",
      verified: true,
    }));

    render(
      <PersonalProductionNotesPanel
        {...panelProps(servicesFor(getNotes), {
          chapter: currentChapter,
          assets: [storyboardAsset("shot-note-1", currentChapter.id)],
        })}
      />,
    );

    expect(await screen.findByText("正在本机核对已加载的媒体信息…")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "按状态筛选" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "定位到记录镜头 1" })).not.toBeInTheDocument();
    await act(async () => {
      captured.resolve({ state: "ready", frames: [] });
      await captured.promise;
    });
    expect(await screen.findByRole("button", { name: "定位到记录镜头 1" })).toBeInTheDocument();
  });

  it.each([
    {
      label: "a no-saved-record readout with a non-empty projection",
      readyText: "你还没有保存个人制作记录。",
      value: readout("无保存记录", {
        mediaState: "ready",
        noSavedRecord: true,
        frames: [readoutFrame(1, "approved", "不应展示的非空投影行")],
      }),
    },
    {
      label: "an orphan-only readout",
      readyText: "孤立旧记录",
      value: readout("孤立旧记录", { mediaState: "ready", frames: [] }),
    },
    {
      label: "an empty current list",
      readyText: "本章暂无分镜。",
      value: readout("空章节", { mediaState: "empty", frames: [] }),
    },
    {
      label: "a mismatched snapshot",
      readyText: "章节分镜或媒体快照与记录不一致，无法确认当前状态。",
      value: readout("不匹配记录", { mediaState: "mismatch", frames: [] }),
    },
  ])("does not expose a locator for $label", async ({ value, readyText }) => {
    const currentChapter: Chapter = {
      ...chapter(),
      content: [{ text: "当前帧", storyboard: ["shot-note-1"] }],
    };
    const getNotes = vi.fn().mockResolvedValue(snapshot(currentChapter.id, "普通记录"));
    projectionMocks.capture.mockResolvedValue({ state: "ready", frames: [] } satisfies CapturedPersonalProductionMedia);
    projectionMocks.project.mockReturnValue(value);

    render(
      <PersonalProductionNotesPanel
        {...panelProps(servicesFor(getNotes), {
          chapter: currentChapter,
          assets: [storyboardAsset("shot-note-1", currentChapter.id)],
        })}
      />,
    );

    expect(await screen.findByRole("region", { name: "我的制作记录" })).toBeInTheDocument();
    expect(await screen.findByText(readyText)).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "按状态筛选" })).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "当前镜头制作记录" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "定位到记录镜头 1" })).not.toBeInTheDocument();
    if (value.noSavedRecord) {
      expect(screen.queryByText("不应展示的非空投影行")).not.toBeInTheDocument();
    }
    expect(getNotes).toHaveBeenCalledTimes(1);
  });

  it("does not offer locating while the request is pending or after a read error", async () => {
    const request = deferred<PersonalProductionSnapshot>();
    const getNotes = vi.fn().mockReturnValueOnce(request.promise);
    const props = panelProps(servicesFor(getNotes));
    const view = render(<PersonalProductionNotesPanel {...props} />);

    expect(await screen.findByText("正在读取个人制作记录…")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "按状态筛选" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "定位到续作镜头" })).not.toBeInTheDocument();
    await act(async () => {
      request.resolve(snapshot("chapter-panel", "读取后记录"));
      await request.promise;
    });
    expect(await screen.findByText("读取后记录")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "定位到续作镜头" })).not.toBeInTheDocument();
    view.unmount();

    const failedServices = servicesFor(vi.fn().mockRejectedValue(new ApiError("http", "failed", 500)));
    render(<PersonalProductionNotesPanel {...panelProps(failedServices)} />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "按状态筛选" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "定位到续作镜头" })).not.toBeInTheDocument();
  });

  it("invalidates an old target immediately on reread and ignores its late 401", async () => {
    const user = userEvent.setup();
    const staleRequest = deferred<PersonalProductionSnapshot>();
    const getNotes = vi.fn()
      .mockResolvedValueOnce(snapshot("chapter-panel", "首次核对"))
      .mockReturnValueOnce(staleRequest.promise)
      .mockResolvedValueOnce(snapshot("chapter-panel", "再次核对"));
    const onLocateResume = vi.fn();
    const onUnauthorized = vi.fn();
    projectionMocks.capture.mockResolvedValue({ state: "ready", frames: [] } satisfies CapturedPersonalProductionMedia);
    projectionMocks.project.mockImplementation((_chapter: Chapter, _captured: CapturedPersonalProductionMedia, value: PersonalProductionSnapshot) => (
      readout(savedNoteText(value.frame_notes.get("orphan")), {
        mediaState: "ready",
        resumePosition: 1,
        status: "needs_reconfirmation",
      })
    ));

    render(
      <PersonalProductionNotesPanel
        {...panelProps(servicesFor(getNotes), { onLocateResume, onUnauthorized })}
      />,
    );
    await user.click(await screen.findByRole("button", { name: "定位到续作镜头" }));
    const oldTarget = onLocateResume.mock.calls[0]?.[0] as PersonalProductionResumeTarget;
    expect(oldTarget.isCurrent()).toBe(true);

    await user.click(screen.getByRole("button", { name: "重新读取记录" }));
    await waitFor(() => expect(getNotes).toHaveBeenCalledTimes(2));
    expect(oldTarget.isCurrent()).toBe(false);
    expect(screen.queryByRole("button", { name: "定位到续作镜头" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重新读取记录" }));
    expect(await screen.findByRole("button", { name: "定位到续作镜头" })).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(3);
    await act(async () => {
      staleRequest.reject(new ApiError("http", "expired", 401));
      await staleRequest.promise.catch(() => undefined);
      await Promise.resolve();
    });

    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(oldTarget.isCurrent()).toBe(false);
    expect(screen.getByRole("button", { name: "定位到续作镜头" })).toBeInTheDocument();
  });

  it.each(["success", "401"] as const)("ignores a late %s after the scope changes", async (outcome) => {
    const request = deferred<PersonalProductionSnapshot>();
    const getNotes = vi.fn().mockReturnValueOnce(request.promise);
    const unauthorized = vi.fn();
    const propsA = panelProps(servicesFor(getNotes), { onUnauthorized: unauthorized });
    const view = render(<PersonalProductionNotesPanel {...propsA} />);
    await waitFor(() => expect(getNotes).toHaveBeenCalledTimes(1));

    const nextServices = servicesFor(vi.fn());
    view.rerender(
      <PersonalProductionNotesPanel
        {...propsA}
        contextToken={{}}
        assetSnapshotToken={{}}
        generation={2}
        chapter={chapter("chapter-B")}
        seriesId="series-B"
        userId="user-B"
        services={nextServices}
      />,
    );
    expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(1);
    expect(nextServices.getPersonalProductionNotes).not.toHaveBeenCalled();

    await act(async () => {
      if (outcome === "success") {
        request.resolve(snapshot("chapter-panel", "旧scope迟到成功"));
        await request.promise;
      } else {
        request.reject(new ApiError("http", "expired", 401));
        await request.promise.catch(() => undefined);
      }
      await Promise.resolve();
    });

    expect(projectionMocks.project).not.toHaveBeenCalled();
    expect(unauthorized).not.toHaveBeenCalled();
    expect(screen.queryByText("旧scope迟到成功")).not.toBeInTheDocument();
  });

  it("ignores a digest that settles after the scope changes", async () => {
    const digest = deferred<CapturedPersonalProductionMedia>();
    const getNotes = vi.fn().mockResolvedValue(snapshot("chapter-panel", "旧摘要"));
    projectionMocks.capture.mockReturnValueOnce(digest.promise);
    const propsA = panelProps(servicesFor(getNotes));
    const view = render(<PersonalProductionNotesPanel {...propsA} />);
    await waitFor(() => expect(projectionMocks.capture).toHaveBeenCalledTimes(1));

    view.rerender(
      <PersonalProductionNotesPanel
        {...propsA}
        contextToken={{}}
        assetSnapshotToken={{}}
        generation={2}
        chapter={chapter("chapter-B")}
        seriesId="series-B"
        userId="user-B"
        services={servicesFor(vi.fn())}
      />,
    );
    expect(screen.queryByText("正在本机核对已加载的媒体信息…")).not.toBeInTheDocument();

    await act(async () => {
      digest.resolve({ state: "empty", frames: [] });
      await digest.promise;
      await Promise.resolve();
    });
    expect(projectionMocks.project).not.toHaveBeenCalled();
    expect(screen.queryByText("旧摘要")).not.toBeInTheDocument();
  });

  it("permanently closes on a first-frame scope change and does not reopen on A-to-B-to-A rerender", async () => {
    const getNotes = vi.fn().mockResolvedValue(snapshot("chapter-panel", "旧用户记录"));
    const close = vi.fn();
    const invalidated = vi.fn();
    const onLocateResume = vi.fn();
    projectionMocks.capture.mockResolvedValue({ state: "ready", frames: [] } satisfies CapturedPersonalProductionMedia);
    projectionMocks.project.mockReturnValue(readout("旧用户记录", {
      mediaState: "ready",
      resumePosition: 1,
    }));
    const propsA = panelProps(servicesFor(getNotes), { onClose: close, onLocateResume, onResumeInvalidated: invalidated });
    const view = render(<PersonalProductionNotesPanel {...propsA} />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "定位到续作镜头" }));
    const oldTarget = onLocateResume.mock.calls[0]?.[0] as PersonalProductionResumeTarget;
    expect(oldTarget.isCurrent()).toBe(true);

    const propsB: PanelProps = {
      ...propsA,
      contextToken: {},
      assetSnapshotToken: {},
      generation: 2,
      chapter: chapter("chapter-B"),
      seriesId: "series-B",
      userId: "user-B",
      assets: [],
      services: servicesFor(vi.fn()),
    };
    view.rerender(<PersonalProductionNotesPanel {...propsB} />);
    expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "定位到续作镜头" })).not.toBeInTheDocument();
    expect(oldTarget.isCurrent()).toBe(false);
    expect(close).toHaveBeenCalledTimes(1);
    expect(invalidated).toHaveBeenCalled();

    view.rerender(<PersonalProductionNotesPanel {...propsA} />);
    expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(1);
    expect(onLocateResume).toHaveBeenCalledTimes(1);
  });

  it("keeps a membership 403 visible and preserves the session until an explicit retry succeeds", async () => {
    const user = userEvent.setup();
    const getNotes = vi.fn()
      .mockRejectedValueOnce(new ApiError("http", "forbidden", 403, "会员资格暂不可用。"))
      .mockResolvedValueOnce(snapshot("chapter-panel", "重试后恢复的记录"));
    const unauthorized = vi.fn();
    render(
      <PersonalProductionNotesPanel
        {...panelProps(servicesFor(getNotes), { onUnauthorized: unauthorized })}
      />,
    );

    expect(await screen.findByRole("heading", { name: "当前账号暂不可查看记录" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("会员资格暂不可用。");
    expect(screen.queryByRole("button", { name: "定位到续作镜头" })).not.toBeInTheDocument();
    expect(unauthorized).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "重试读取" }));
    expect(await screen.findByText("重试后恢复的记录")).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(2);
    expect(unauthorized).not.toHaveBeenCalled();
  });
});
