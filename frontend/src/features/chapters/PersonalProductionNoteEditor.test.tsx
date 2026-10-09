import { useState } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter } from "../../shared/api/contracts";
import {
  parsePersonalProductionSnapshot,
  type PersonalProductionNoteStatus,
} from "../../shared/api/personalProductionNotes";
import type { CapturedPersonalProductionMedia, PersonalProductionReadout } from "./personal-production/projection";
import type { PersonalProductionNoteEditCandidate } from "./personal-production/noteEdit";
import { PersonalProductionNoteEditor, type PersonalProductionNoteEditorDraft } from "./PersonalProductionNoteEditor";

afterEach(() => cleanup());

function makeCandidate(approvalEligible = true): PersonalProductionNoteEditCandidate {
  const chapterId = "editor-test-chapter";
  const seriesId = "editor-test-series";
  const frameIds = ["editor-test-frame-1", "editor-test-frame-2"];
  const chapter: Chapter = {
    id: chapterId,
    series_id: seriesId,
    title: "编辑器测试章节",
    content: frameIds.map((id) => ({ text: "镜头文字", storyboard: [id] })),
    order: 1,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    lock: null,
  };
  const snapshot = parsePersonalProductionSnapshot({
    chapter_id: chapterId,
    revision: 1,
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
      [frameIds[1]!]: { status: "needs_revision", note: "当前备注" },
    },
    resume_frame_id: frameIds[0],
  }, chapterId);
  const captured: CapturedPersonalProductionMedia = {
    state: "ready",
    frames: frameIds.map((id, frameIndex) => ({
      frameIndex,
      candidateId: id,
      storyboardAssetId: id,
      identityValid: true,
      assetImageUrl: null,
      previewUrl: null,
      assetImageDigest: null,
      previewDigest: null,
      digestAvailable: true,
    })),
  };
  const row: PersonalProductionReadout["frames"][number] = {
    position: 2,
    status: "needs_revision",
    note: "当前备注",
    hasSavedNote: true,
    verified: true,
  };
  const readout: PersonalProductionReadout = {
    revision: 1,
    noSavedRecord: false,
    mediaState: "ready",
    frames: [
      { ...row, position: 1, note: "第一镜头备注" },
      row,
    ],
    orphanNotes: [],
    resumePosition: 1,
    resumeIsInvalid: false,
  };
  return {
    chapterId,
    frameIndex: 1,
    position: 2,
    storyboardAssetId: frameIds[1]!,
    expectedRevision: 1,
    expectedMediaRevision: 1,
    note: "当前备注",
    initialStatus: "needs_revision",
    approvalEligible,
    row,
    frame: chapter.content![1]!,
    capturedFrame: captured.frames[1]!,
    snapshot,
    captured,
    readout,
  };
}

function draft(overrides: Partial<PersonalProductionNoteEditorDraft> = {}): PersonalProductionNoteEditorDraft {
  return {
    status: "needs_revision",
    note: "当前备注",
    requiresStatusConfirmation: false,
    ...overrides,
  };
}

function ControlledEditor({
  candidate,
  initialDraft,
  onSave,
}: {
  candidate: PersonalProductionNoteEditCandidate;
  initialDraft: PersonalProductionNoteEditorDraft;
  onSave: () => void;
}) {
  const [value, setValue] = useState(initialDraft);
  return (
    <PersonalProductionNoteEditor
      candidate={candidate}
      draft={value}
      disabled={false}
      onStatusChange={(status) => setValue((current) => ({ ...current, status }))}
      onNoteChange={(note) => setValue((current) => ({ ...current, note }))}
      onSave={onSave}
      onCancel={vi.fn()}
    />
  );
}

describe("PersonalProductionNoteEditor", () => {
  it("keeps note content literal, permits an empty note, and calls save only on request", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    const hostileLookingText = '<img src=x onerror="alert(1)"> & <b>原文</b>';
    const { container } = render(
      <ControlledEditor
        candidate={makeCandidate()}
        initialDraft={draft({ note: hostileLookingText })}
        onSave={onSave}
      />,
    );

    const note = screen.getByRole("textbox", { name: "镜头 2 的文字备注" });
    expect(note).toHaveValue(hostileLookingText);
    expect(container.querySelector("img, b, script")).toBeNull();
    expect(screen.getByRole("button", { name: "保存镜头记录" })).toBeEnabled();

    await user.clear(note);
    expect(note).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "保存镜头记录" }));
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it("counts supplementary Unicode code points and blocks notes beyond 2,000", async () => {
    const exactLimit = "😀".repeat(2_000);
    const onSave = vi.fn();
    const props = {
      candidate: makeCandidate(),
      disabled: false,
      onStatusChange: vi.fn(),
      onNoteChange: vi.fn(),
      onSave,
      onCancel: vi.fn(),
    };
    const { rerender } = render(
      <PersonalProductionNoteEditor {...props} draft={draft({ note: exactLimit })} />,
    );

    expect(screen.getByText("2000/2000 字")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存镜头记录" })).toBeEnabled();

    rerender(
      <PersonalProductionNoteEditor {...props} draft={draft({ note: exactLimit + "😀" })} />,
    );
    expect(screen.getByText("2001/2000 字")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("备注最多 2000 字。");
    expect(screen.getByRole("button", { name: "保存镜头记录" })).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("requires an explicit status and disables approval when media identity is not eligible", () => {
    const onSave = vi.fn();
    render(
      <PersonalProductionNoteEditor
        candidate={makeCandidate(false)}
        draft={draft({ status: null, requiresStatusConfirmation: true })}
        disabled={false}
        onStatusChange={vi.fn()}
        onNoteChange={vi.fn()}
        onSave={onSave}
        onCancel={vi.fn()}
      />,
    );

    const status = screen.getByRole("combobox", { name: "镜头 2 的制作状态" });
    expect(status).toHaveValue("");
    expect(screen.getByRole("status")).toHaveTextContent("重新明确选择制作状态");
    expect(screen.getByRole("option", { name: "已认可" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "保存镜头记录" })).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("does not allow editing or saving while the owner marks the editor disabled", async () => {
    const user = userEvent.setup();
    const onStatusChange = vi.fn<(status: PersonalProductionNoteStatus | null) => void>();
    const onNoteChange = vi.fn<(note: string) => void>();
    const onSave = vi.fn();
    const onCancel = vi.fn();
    render(
      <PersonalProductionNoteEditor
        candidate={makeCandidate()}
        draft={draft()}
        disabled
        onStatusChange={onStatusChange}
        onNoteChange={onNoteChange}
        onSave={onSave}
        onCancel={onCancel}
      />,
    );

    expect(screen.getByRole("combobox", { name: "镜头 2 的制作状态" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "镜头 2 的文字备注" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "保存镜头记录" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "关闭编辑器" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "保存镜头记录" }));
    expect(onStatusChange).not.toHaveBeenCalled();
    expect(onNoteChange).not.toHaveBeenCalled();
    expect(onSave).not.toHaveBeenCalled();
    expect(onCancel).not.toHaveBeenCalled();
  });
});
