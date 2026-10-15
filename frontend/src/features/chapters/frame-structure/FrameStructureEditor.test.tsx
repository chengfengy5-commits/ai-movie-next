import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Chapter, Character, Prop, Scene } from "../../../shared/api/contracts";
import {
  FrameStructureEditor,
  type FrameStructureMode,
  type FrameStructureSubmitResult,
  type FrameStructureVerifyResult,
} from "./FrameStructureEditor";
import type { FrameStructureDraft } from "./contract";

type SubmitHandler = (draft: FrameStructureDraft | null, signal: AbortSignal) => Promise<FrameStructureSubmitResult>;
type VerifyHandler = (signal: AbortSignal) => Promise<FrameStructureVerifyResult>;

const chapter: Chapter = {
  id: "chapter-1",
  series_id: "series-1",
  title: "第一章",
  content: [{ text: "首镜", storyboard: ["frame-1"] }],
  order: 1,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-02T00:00:00Z",
  lock: null,
};

const characters: Character[] = [{
  id: "character-a",
  series_id: "series-1",
  name: "林舟",
  gender: null,
  age: null,
  role: "主角",
  appearance: null,
  description: null,
  image_url: null,
  audio_url: null,
  voice_ref: null,
  aliases: null,
  canonical_key: null,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-02T00:00:00Z",
}];
const scenes: Scene[] = [{
  id: "scene-a",
  series_id: "series-1",
  title: "旧街",
  description: null,
  image_url: null,
  aliases: null,
  canonical_key: null,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-02T00:00:00Z",
}];
const props: Prop[] = [{
  id: "prop-a",
  series_id: "series-1",
  name: "信封",
  description: null,
  image_url: null,
  aliases: null,
  canonical_key: null,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-02T00:00:00Z",
}];

function renderEditor(
  mode: FrameStructureMode = { kind: "insert", afterPosition: 0 },
  onSubmit: SubmitHandler = vi.fn(async () => ({ status: "saved" as const })),
  onVerify: VerifyHandler = vi.fn(async () => ({ status: "not-applied" as const, message: "服务器内容未变化。" })),
) {
  const lifecycle = new AbortController();
  const onClose = vi.fn();
  const view = render(
    <FrameStructureEditor
      chapter={chapter}
      characters={characters}
      lifecycleSignal={lifecycle.signal}
      lockNotice="已取得章节编辑锁。"
      mode={mode}
      onClose={onClose}
      onSubmit={onSubmit}
      onVerify={onVerify}
      props={props}
      scenes={scenes}
    />,
  );
  return { ...view, lifecycle, onClose, onSubmit, onVerify };
}

describe("FrameStructureEditor", () => {
  afterEach(() => cleanup());

  it("captures only plain frame text and selected same-series asset IDs", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async (_draft: FrameStructureDraft | null, _signal: AbortSignal): Promise<FrameStructureSubmitResult> => ({
      status: "saved" as const,
      message: "已保存。",
    }));
    const { onClose } = renderEditor({ kind: "insert", afterPosition: 1 }, onSubmit);

    await user.type(screen.getByLabelText("分镜文字"), "雨停后，林舟推开旧街的门。");
    await user.click(screen.getByLabelText("林舟"));
    await user.click(screen.getByLabelText("旧街"));
    await user.click(screen.getByLabelText("信封"));
    await user.click(screen.getByRole("button", { name: "确认插入" }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0]?.[0]).toEqual({
      text: "雨停后，林舟推开旧街的门。",
      characterAssetIds: ["character-a"],
      sceneAssetIds: ["scene-a"],
      propAssetIds: ["prop-a"],
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("keeps a baseline verification result unknown and never enables another delete", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async (_draft: FrameStructureDraft | null, _signal: AbortSignal): Promise<FrameStructureSubmitResult> => ({
      status: "unknown",
      message: "写入结果未知。",
    }));
    const onVerify = vi.fn(async () => ({ status: "unknown" as const, message: "服务器仍是修改前内容，但请求可能迟到完成。" }));
    const { onClose } = renderEditor({ kind: "delete", position: 1 }, onSubmit, onVerify);

    await user.click(screen.getByRole("button", { name: "确认删除" }));
    expect(await screen.findByText("写入结果未知。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认删除" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "只核对服务器状态" }));
    expect(onVerify).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/请求可能迟到完成/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认删除" })).not.toBeInTheDocument();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("asks before discarding typed text and keeps the draft when editing continues", async () => {
    const user = userEvent.setup();
    const { onClose } = renderEditor();
    const textbox = screen.getByRole("textbox", { name: "分镜文字" });
    await user.type(textbox, "未保存镜头");
    await user.click(screen.getByRole("button", { name: "取消" }));

    const confirmation = await screen.findByRole("alertdialog", { name: "放弃分镜草稿？" });
    expect(confirmation).toHaveTextContent("当前输入和素材选择尚未保存");
    await user.click(within(confirmation).getByRole("button", { name: "继续编辑" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(textbox).toHaveValue("未保存镜头");
    expect(onClose).not.toHaveBeenCalled();

    await user.keyboard("{Escape}");
    const reopened = await screen.findByRole("alertdialog", { name: "放弃分镜草稿？" });
    await user.click(within(reopened).getByRole("button", { name: "放弃草稿并离开" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("uses the same discard confirmation for selected assets and Escape", async () => {
    const user = userEvent.setup();
    const { onClose } = renderEditor();
    const characterChoice = screen.getByLabelText("林舟");
    await user.click(characterChoice);
    fireEvent.keyDown(document, { key: "Escape" });

    const confirmation = await screen.findByRole("alertdialog", { name: "放弃分镜草稿？" });
    await user.click(within(confirmation).getByRole("button", { name: "继续编辑" }));
    expect(characterChoice).toBeChecked();
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.keyDown(document, { key: "Escape" });
    await user.click(await screen.findByRole("button", { name: "放弃草稿并离开" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("keeps the dialog keyboard accessible at a narrow viewport", async () => {
    const previousWidth = window.innerWidth;
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    const user = userEvent.setup();
    const { onClose } = renderEditor();
    try {
      const dialog = screen.getByRole("dialog", { name: "插入分镜" });
      expect(dialog).toBeInTheDocument();
      expect(screen.getByRole("group", { name: "角色素材" })).toBeInTheDocument();
      expect(screen.getByRole("group", { name: "场景素材" })).toBeInTheDocument();
      expect(screen.getByRole("group", { name: "道具素材" })).toBeInTheDocument();
      fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "确认插入" }));

      const textbox = screen.getByRole("textbox", { name: "分镜文字" });
      await user.type(textbox, "窄屏待保存草稿");
      await user.keyboard("{Escape}");
      let confirmation = await screen.findByRole("alertdialog", { name: "放弃分镜草稿？" });
      const continueEditing = within(confirmation).getByRole("button", { name: "继续编辑" });
      const discardDraft = within(confirmation).getByRole("button", { name: "放弃草稿并离开" });
      expect(document.activeElement).toBe(continueEditing);
      await user.keyboard("{Tab}");
      expect(document.activeElement).toBe(discardDraft);
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
      expect(textbox).toHaveValue("窄屏待保存草稿");
      expect(onClose).not.toHaveBeenCalled();

      await user.keyboard("{Escape}");
      confirmation = await screen.findByRole("alertdialog", { name: "放弃分镜草稿？" });
      await user.click(within(confirmation).getByRole("button", { name: "放弃草稿并离开" }));
      await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    } finally {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: previousWidth });
    }
  });
});
