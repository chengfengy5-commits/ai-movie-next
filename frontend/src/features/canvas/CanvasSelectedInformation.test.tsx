// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CanvasSelectedInformation } from "./CanvasSelectedInformation";

afterEach(cleanup);

describe("CanvasSelectedInformation", () => {
  it("shows complete selected content and exposes locate and edit actions", () => {
    const onLocate = vi.fn();
    const onEdit = vi.fn();
    const onDelete = vi.fn();
    render(
      <CanvasSelectedInformation
        deleteAction={{ label: "删除便签", onDelete }}
        fields={[{ label: "所属章节", value: "第一章" }]}
        fullText={"第一行\n第二行"}
        kindLabel="镜头"
        preparationProgress={75}
        relations={[{
          key: "character:character-a",
          relationship: "引用素材",
          label: "林澜",
          onLocate,
          onEdit,
          editLabel: "编辑素材",
        }]}
        title="分镜 1"
      />,
    );

    const information = screen.getByRole("region", { name: "选中内容信息" });
    expect(within(information).getByText((text) => text.includes("第一行") && text.includes("第二行"))).toBeTruthy();
    expect(information.querySelector(".canvas-selected-information-progress strong")?.textContent).toBe("75%");
    expect(within(information).getByText("第一章")).toBeTruthy();
    fireEvent.click(within(information).getByRole("button", { name: "定位" }));
    fireEvent.click(within(information).getByRole("button", { name: "编辑素材" }));
    fireEvent.click(within(information).getByRole("button", { name: "删除便签" }));
    expect(onLocate).toHaveBeenCalledOnce();
    expect(onEdit).toHaveBeenCalledOnce();
    expect(onDelete).toHaveBeenCalledOnce();
  });

  it("explains when a related source cannot be edited", () => {
    render(
      <CanvasSelectedInformation
        kindLabel="镜头"
        primaryAction={{ label: "编辑分镜文字", disabledReason: "请先保存画布草稿。" }}
        relations={[{
          key: "asset:unknown",
          relationship: "引用素材",
          label: "无法确认唯一节点",
          locateDisabledReason: "未按名称或旧位置猜测。",
          editLabel: "编辑素材",
          editDisabledReason: "素材身份未能唯一确认。",
        }]}
        title="分镜 1"
      />,
    );

    const information = screen.getByRole("region", { name: "选中内容信息" });
    expect(within(information).getByRole("button", { name: "编辑分镜文字" })).toBeDisabled();
    expect(within(information).getByText("请先保存画布草稿。")).toBeTruthy();
    expect(within(information).getByRole("button", { name: "定位" })).toBeDisabled();
    expect(within(information).getByRole("button", { name: "编辑素材" })).toBeDisabled();
    expect(within(information).getByText("未按名称或旧位置猜测。")).toBeTruthy();
  });
});
