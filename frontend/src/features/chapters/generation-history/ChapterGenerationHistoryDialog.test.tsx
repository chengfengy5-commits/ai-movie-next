// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import type { ChapterGenerationHistoryMessage } from "../../../shared/api/chapterGenerationHistory";
import { ChapterGenerationHistoryDialog } from "./ChapterGenerationHistoryDialog";

const message: ChapterGenerationHistoryMessage = {
  id: "history-message-dialog-1200",
  chapter_id: "history-chapter-dialog-1200",
  frame_index: 2,
  asset_type: null,
  asset_id: null,
  chat_mode: "image",
  role: "assistant",
  content: "https://cdn.example.test/history/image.png?signature=preserved",
  model_name: "history-model",
  created_at: "2026-10-28T10:00:00Z",
  message_digest: "a".repeat(64),
};

const entry = {
  message,
  mediaUrl: message.content,
  unsafeMedia: false,
  canAdopt: true,
} as const;

function createProps(overrides: Partial<ComponentProps<typeof ChapterGenerationHistoryDialog>> = {}) {
  return {
    ownerKey: "dialog-owner-1200",
    chapterTitle: "第一章",
    frameIndex: 2,
    targetKind: "frame-preview" as const,
    mode: "image" as const,
    loadState: { status: "ready" as const, entries: [entry] },
    page: 0,
    pageCount: 1,
    writable: true,
    readOnlyReason: null,
    confirmation: null,
    busy: false,
    notice: null,
    previewAssetName: "镜头 3",
    isCurrent: () => true,
    readMedia: vi.fn(async () => new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" })),
    onUnauthorized: vi.fn(),
    onClose: vi.fn(),
    onTargetKindChange: vi.fn(),
    onModeChange: vi.fn(),
    onPageChange: vi.fn(),
    onPrepareAdoption: vi.fn(),
    onCancelConfirmation: vi.fn(),
    onConfirmAdoption: vi.fn(),
    onVerifyUnknown: vi.fn(),
    ...overrides,
  } satisfies ComponentProps<typeof ChapterGenerationHistoryDialog>;
}

const originalCreateObjectUrl = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const originalRevokeObjectUrl = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  if (originalCreateObjectUrl === undefined) delete (URL as unknown as { createObjectURL?: unknown }).createObjectURL;
  else Object.defineProperty(URL, "createObjectURL", originalCreateObjectUrl);
  if (originalRevokeObjectUrl === undefined) delete (URL as unknown as { revokeObjectURL?: unknown }).revokeObjectURL;
  else Object.defineProperty(URL, "revokeObjectURL", originalRevokeObjectUrl);
});

describe("ChapterGenerationHistoryDialog", () => {
  it("exposes the separate history source and image/video mode choices", () => {
    const props = createProps();
    render(<ChapterGenerationHistoryDialog {...props} />);

    fireEvent.click(screen.getByRole("button", { name: "故事板素材" }));
    fireEvent.click(screen.getByRole("button", { name: "视频" }));

    expect(props.onTargetKindChange).toHaveBeenCalledWith("storyboard-image");
    expect(props.onModeChange).toHaveBeenCalledWith("video");
    expect(screen.getByText("选用后只更新镜头预览字段；历史浏览不会修改章节或素材。")).toBeTruthy();
  });

  it("requires a separate confirmation before one adoption callback", () => {
    const props = createProps();
    const view = render(<ChapterGenerationHistoryDialog {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "选用此图片结果" }));
    expect(props.onPrepareAdoption).toHaveBeenCalledWith(message);
    expect(props.onConfirmAdoption).not.toHaveBeenCalled();

    const confirmationProps = createProps({
      confirmation: { messageId: message.id, currentMediaUrl: null, alreadyCurrent: false },
    });
    view.rerender(<ChapterGenerationHistoryDialog {...confirmationProps} />);
    fireEvent.click(screen.getByRole("button", { name: "确认替换镜头预览" }));
    expect(confirmationProps.onConfirmAdoption).toHaveBeenCalledTimes(1);
    expect(screen.getByText("确认将此历史结果写入当前镜头预览？")).toBeTruthy();
  });

  it("keeps ambiguous targets browse-only and offers read-only verification for unknown writes", () => {
    const props = createProps({
      writable: false,
      readOnlyReason: "duplicate_storyboard_id",
      notice: { kind: "unknown", message: "请求结果未知。", canVerify: true },
    });
    render(<ChapterGenerationHistoryDialog {...props} />);

    expect(screen.getByText("故事板素材编号重复，只能浏览。")).toBeTruthy();
    expect(screen.getByRole("button", { name: "选用此图片结果" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "只读核对当前值" }));
    expect(props.onVerifyUnknown).toHaveBeenCalledTimes(1);
    expect(props.onConfirmAdoption).not.toHaveBeenCalled();
  });

  it("keeps unreadable chapter history browse-only", () => {
    const props = createProps({ writable: false, readOnlyReason: "unreadable_chapter" });
    render(<ChapterGenerationHistoryDialog {...props} />);

    expect(screen.getByText("章节内容无法安全识别，只能浏览，不能选用历史结果。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "选用此图片结果" })).toBeDisabled();
    expect(props.onConfirmAdoption).not.toHaveBeenCalled();
  });

  it("reads history media into an object URL and revokes it when closed", async () => {
    const createObjectURL = vi.fn(() => "blob:chapter-history-preview");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    const props = createProps();
    const view = render(<ChapterGenerationHistoryDialog {...props} />);
    expect(props.readMedia).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "预览图片历史" }));

    await waitFor(() => expect(screen.getByRole("img", { name: "镜头 3生成图片" }).getAttribute("src"))
      .toBe("blob:chapter-history-preview"));
    expect(props.readMedia).toHaveBeenCalledWith(message.content, expect.any(AbortSignal));

    view.unmount();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:chapter-history-preview");
  });

  it("keeps only the explicitly opened history preview mounted", async () => {
    const secondEntry = {
      ...entry,
      message: { ...message, id: "history-message-dialog-second-1200" },
    };
    const props = createProps({ loadState: { status: "ready", entries: [entry, secondEntry] } });
    render(<ChapterGenerationHistoryDialog {...props} />);

    expect(props.readMedia).not.toHaveBeenCalled();
    fireEvent.click(screen.getAllByRole("button", { name: "预览图片历史" })[0]!);
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "预览图片历史" }));
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(1));
    expect(screen.getAllByRole("button", { name: "关闭图片预览" })).toHaveLength(1);
  });
});
