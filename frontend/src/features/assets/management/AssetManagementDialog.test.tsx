import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Character, Prop, Scene } from "../../../shared/api/contracts";
import type { WorkspaceServices } from "../../../shared/api/services";
import type { CanvasCreationReceipt } from "../../../shared/api/canvasSources";
import { AssetManagementDialog, type GuardedAssetMutation } from "./AssetManagementDialog";

const timestamp = "2026-10-24T12:00:00Z";

function entityFor(category: "characters" | "scenes" | "props", id: string): Character | Scene | Prop {
  const base = {
    id,
    series_id: "series-a",
    description: "synthetic description",
    image_url: "https://example.invalid/image.png",
    aliases: null,
    canonical_key: null,
    created_at: timestamp,
    updated_at: timestamp,
  };
  if (category === "characters") {
    return {
      ...base,
      name: "林澜",
      gender: null,
      age: null,
      role: null,
      appearance: null,
      audio_url: null,
      voice_ref: null,
    };
  }
  if (category === "scenes") return { ...base, title: "旧书店" };
  return { ...base, name: "铜钥匙" };
}

afterEach(() => cleanup());

describe("AssetManagementDialog guarded source editing", () => {
  it.each([
    { category: "characters" as const, nameLabel: "角色名称", name: "林澜" },
    { category: "scenes" as const, nameLabel: "场景名称", name: "旧书店" },
    { category: "props" as const, nameLabel: "道具名称", name: "铜钥匙" },
  ])("creates a $category with an image URL through the guarded mutation and returns the actual entity", async ({
    category,
    nameLabel,
    name,
  }) => {
    const entity = entityFor(category, `created-${category}-id`);
    const receipt: CanvasCreationReceipt = {
      token: "opaque-server-created-asset-proof",
      kind: category === "characters" ? "character" : category === "scenes" ? "scene" : "prop",
      target: { id: entity.id },
    };
    const guardedMutation: GuardedAssetMutation = {
      save: vi.fn(async () => ({ entity, receipt })),
      verify: vi.fn(async () => "只读核对完成。"),
    };
    const services = {} as WorkspaceServices;
    const onConfirmedEntity = vi.fn();
    const onClose = vi.fn();

    render(
      <AssetManagementDialog
        category={category}
        guardedMutation={guardedMutation}
        isCurrent={() => true}
        item={null}
        mode="create"
        onClose={onClose}
        onConfirmedEntity={onConfirmedEntity}
        onDirectoryNeedsRefresh={vi.fn()}
        onDirectoryUpdated={vi.fn()}
        onUnauthorized={vi.fn()}
        seriesId="series-a"
        seriesName="测试剧集"
        services={services}
      />,
    );

    const dialog = within(screen.getByRole("dialog", { name: "新增" + (
      category === "characters" ? "角色" : category === "scenes" ? "场景" : "道具"
    ) }));
    fireEvent.change(dialog.getByLabelText(new RegExp(nameLabel, "u")), { target: { value: name } });
    fireEvent.change(dialog.getByLabelText("图片 URL"), {
      target: { value: "https://example.invalid/cover.png" },
    });
    fireEvent.click(dialog.getByRole("button", { name: "创建素材" }));

    await waitFor(() => expect(guardedMutation.save).toHaveBeenCalledOnce());
    expect(guardedMutation.save).toHaveBeenCalledWith({
      mode: "create",
      category,
      item: null,
      values: expect.objectContaining({
        [category === "scenes" ? "title" : "name"]: name,
        image_url: "https://example.invalid/cover.png",
      }),
    }, expect.any(AbortSignal));
    expect(onConfirmedEntity).toHaveBeenCalledWith(entity, receipt);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("includes an edited image URL in the guarded update and reports the same asset ID", async () => {
    const original = entityFor("characters", "character-existing-id") as Character;
    const updated = { ...original, image_url: "https://example.invalid/updated.png" };
    const guardedMutation: GuardedAssetMutation = {
      save: vi.fn(async () => ({ entity: updated, receipt: null })),
      verify: vi.fn(async () => "只读核对完成。"),
    };
    const onConfirmedEntity = vi.fn();

    render(
      <AssetManagementDialog
        category="characters"
        guardedMutation={guardedMutation}
        isCurrent={() => true}
        item={original}
        mode="edit"
        onClose={vi.fn()}
        onConfirmedEntity={onConfirmedEntity}
        onDirectoryNeedsRefresh={vi.fn()}
        onDirectoryUpdated={vi.fn()}
        onUnauthorized={vi.fn()}
        seriesId="series-a"
        seriesName="测试剧集"
        services={{} as WorkspaceServices}
      />,
    );

    const dialog = within(screen.getByRole("dialog", { name: "编辑角色" }));
    fireEvent.change(dialog.getByLabelText("图片 URL"), {
      target: { value: updated.image_url ?? "" },
    });
    fireEvent.click(dialog.getByRole("button", { name: "保存更改" }));

    await waitFor(() => expect(guardedMutation.save).toHaveBeenCalledOnce());
    expect(guardedMutation.save).toHaveBeenCalledWith({
      mode: "edit",
      category: "characters",
      item: original,
      values: { image_url: updated.image_url },
    }, expect.any(AbortSignal));
    expect(onConfirmedEntity).toHaveBeenCalledWith(updated, null);
  });
});
