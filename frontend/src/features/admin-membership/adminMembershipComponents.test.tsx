import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApiError } from "../../shared/api/errors";
import type {
  AdminMembershipApi,
  AdminPurchasePackage,
  AdminRedemptionCode,
  GenerateRedemptionCodesReceipt,
} from "./adminMembershipTypes";
import { RedemptionCodes, type RedemptionCodesProps } from "./RedemptionCodes";
import { PurchasePackages, type PurchasePackagesProps } from "./PurchasePackages";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function code(overrides: Partial<AdminRedemptionCode> = {}): AdminRedemptionCode {
  return {
    id: "code-1", code: "SYNTHETIC-CODE", type: "membership", value: 30, is_used: false,
    used_by: null, used_at: null, created_by: "admin-1", expires_at: null, created_at: "created",
    ...overrides,
  };
}

function packageRecord(overrides: Partial<AdminPurchasePackage> = {}): AdminPurchasePackage {
  return {
    id: "package-1", category: "credits", name: "Legacy package", price: -8,
    credits: 0, days: -2, sort_order: 0, is_active: true, created_at: "created", updated_at: "updated",
    ...overrides,
  };
}

function membershipApi(overrides: Partial<AdminMembershipApi> = {}): AdminMembershipApi {
  return {
    listRedemptionCodes: vi.fn<AdminMembershipApi["listRedemptionCodes"]>(async () => ({ data: [], total: 0 })),
    getRedemptionCode: vi.fn<AdminMembershipApi["getRedemptionCode"]>(async () => code()),
    generateRedemptionCodes: vi.fn<AdminMembershipApi["generateRedemptionCodes"]>(async () => ({
      record: code(), generated_count: 1,
    })),
    deleteRedemptionCode: vi.fn<AdminMembershipApi["deleteRedemptionCode"]>(async () => ({ message: "删除成功" })),
    listPurchasePackages: vi.fn<AdminMembershipApi["listPurchasePackages"]>(async () => ({ data: [], total: 0 })),
    getPurchasePackage: vi.fn<AdminMembershipApi["getPurchasePackage"]>(async () => packageRecord()),
    createPurchasePackage: vi.fn<AdminMembershipApi["createPurchasePackage"]>(async () => packageRecord()),
    updatePurchasePackage: vi.fn<AdminMembershipApi["updatePurchasePackage"]>(async () => packageRecord()),
    deletePurchasePackage: vi.fn<AdminMembershipApi["deletePurchasePackage"]>(async () => ({ message: "删除成功" })),
    ...overrides,
  };
}

function redemptionProps(api: AdminMembershipApi, overrides: Partial<RedemptionCodesProps> = {}): RedemptionCodesProps {
  return {
    api, mode: "list", ownerScope: "admin-1/session-1", viewKey: "redemption/list",
    currentAdministratorId: "admin-1", onAuthenticationError: vi.fn(), onCreate: vi.fn(),
    onCancel: vi.fn(), onSaved: vi.fn(), ...overrides,
  };
}

function packageProps(api: AdminMembershipApi, overrides: Partial<PurchasePackagesProps> = {}): PurchasePackagesProps {
  return {
    api, mode: "list", ownerScope: "admin-1/session-1", viewKey: "packages/list",
    currentAdministratorId: "admin-1", onAuthenticationError: vi.fn(), onCreate: vi.fn(),
    onEdit: vi.fn(), onCancel: vi.fn(), onSaved: vi.fn(), ...overrides,
  };
}

describe("admin membership resource views", () => {
  it("sends redemption false and zero filters to the server and keeps the 25-row page size", async () => {
    const list = vi.fn<AdminMembershipApi["listRedemptionCodes"]>(async () => ({
      data: [code({ value: 0 })], total: 1,
    }));
    render(<RedemptionCodes {...redemptionProps(membershipApi({ listRedemptionCodes: list }))} />);

    await screen.findByText("SYNTHETIC-CODE");
    expect(list.mock.calls[0]?.[0]).toMatchObject({
      skip: 0, limit: 25, sort_field: "created_at", sort_order: "desc",
    });

    fireEvent.change(screen.getByLabelText("使用状态"), { target: { value: "false" } });
    fireEvent.change(screen.getByLabelText("面值"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "应用筛选" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(
      expect.objectContaining({ is_used: false, value: 0, skip: 0, limit: 25 }),
      expect.any(AbortSignal),
    ));
  });

  it("guards a same-tick double submit and reports only the server's first-record generation receipt", async () => {
    const pending = deferred<GenerateRedemptionCodesReceipt>();
    const generate = vi.fn<AdminMembershipApi["generateRedemptionCodes"]>(() => pending.promise);
    const onSaved = vi.fn();
    render(<RedemptionCodes {...redemptionProps(membershipApi({ generateRedemptionCodes: generate }), {
      mode: "create", viewKey: "redemption/create", onSaved,
    })} />);

    fireEvent.change(screen.getByLabelText("面值"), { target: { value: "30" } });
    fireEvent.change(screen.getByLabelText("数量"), { target: { value: "2" } });
    const form = screen.getByLabelText("面值").closest("form");
    if (!form) throw new Error("expected generation form");
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(generate).toHaveBeenCalledTimes(1);
    expect(generate).toHaveBeenCalledWith(
      { type: "membership", value: 30, count: 2, expires_at: null },
      "admin-1", expect.any(AbortSignal),
    );

    await act(async () => pending.resolve({ record: code({ code: "SYNTHETIC-FIRST" }), generated_count: 2 }));
    expect(await screen.findByText("服务端已确认生成 2 个兑换码。")).toBeTruthy();
    expect(screen.getByText("SYNTHETIC-FIRST")).toBeTruthy();
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "生成" })).toBeDisabled();
  });

  it("freezes an uncertain redemption create, offers only a read-only directory check, and hands off the pending intent", async () => {
    const list = vi.fn<AdminMembershipApi["listRedemptionCodes"]>(async () => ({ data: [code()], total: 1 }));
    const generate = vi.fn<AdminMembershipApi["generateRedemptionCodes"]>(async () => {
      throw new ApiError("network", "网络中断");
    });
    const onCancel = vi.fn();
    render(<RedemptionCodes {...redemptionProps(membershipApi({ listRedemptionCodes: list, generateRedemptionCodes: generate }), {
      mode: "create", viewKey: "redemption/create", onCancel,
    })} />);

    fireEvent.change(screen.getByLabelText("面值"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    expect(await screen.findByText("生成结果待核对", { selector: "strong" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "生成" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "只读查询兑换码目录" }));
    expect(await screen.findByRole("table", { name: "只读兑换码目录核对" })).toBeTruthy();
    expect(list).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "返回目录" }));
    expect(onCancel).toHaveBeenCalledWith(expect.objectContaining({
      kind: "unknown-write", resource: "redemption-codes",
    }));
  });

  it("preserves negative, zero, false and legacy package values in the list without fake filters", async () => {
    const list = vi.fn<AdminMembershipApi["listPurchasePackages"]>(async () => ({
      data: [packageRecord({ category: "legacy-category", price: -8, credits: 0, days: -2, is_active: false })],
      total: 1,
    }));
    render(<PurchasePackages {...packageProps(membershipApi({ listPurchasePackages: list }))} />);

    expect(await screen.findByText("legacy-category")).toBeTruthy();
    expect(screen.getByText("-8")).toBeTruthy();
    expect(screen.getByText("-2")).toBeTruthy();
    expect(screen.getByText("停用")).toBeTruthy();
    expect(list.mock.calls[0]?.[0]).toEqual({
      skip: 0, limit: 10, sort_field: "sort_order", sort_order: "asc",
    });
  });

  it("sends only changed package fields, including explicit null, zero, and false", async () => {
    const original = packageRecord({ price: -8, credits: 5, days: -2, is_active: true });
    const get = vi.fn<AdminMembershipApi["getPurchasePackage"]>(async () => original);
    const update = vi.fn<AdminMembershipApi["updatePurchasePackage"]>(async (_id, patch) => ({
      ...original, ...patch,
    }));
    render(<PurchasePackages {...packageProps(membershipApi({ getPurchasePackage: get, updatePurchasePackage: update }), {
      mode: "edit", recordId: original.id, viewKey: "packages/edit/package-1",
    })} />);

    await waitFor(() => expect(screen.getByLabelText("价格（整数元）")).toHaveValue("-8"));
    fireEvent.change(screen.getByRole("combobox", { name: /类别/ }), { target: { value: "membership" } });
    fireEvent.change(screen.getByLabelText("价格（整数元）"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("积分（空值表示 null）"), { target: { value: "" } });
    fireEvent.click(screen.getByLabelText("启用"));
    fireEvent.click(screen.getByRole("button", { name: "保存更改" }));

    await waitFor(() => expect(update).toHaveBeenCalledWith(original.id, {
      category: "membership", price: 0, credits: null, is_active: false,
    }, expect.any(AbortSignal)));
    expect(update.mock.calls[0]?.[1]).not.toHaveProperty("days");
  });

  it("does not PUT an unchanged edit and preserves its source values", async () => {
    const original = packageRecord({ credits: 0, days: null, is_active: false });
    const update = vi.fn<AdminMembershipApi["updatePurchasePackage"]>();
    render(<PurchasePackages {...packageProps(membershipApi({
      getPurchasePackage: vi.fn(async () => original), updatePurchasePackage: update,
    }), {
      mode: "edit", recordId: original.id, viewKey: "packages/edit/unchanged",
    })} />);

    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue(original.name));
    expect(screen.getByRole("button", { name: "保存更改" })).toBeDisabled();
    expect(update).not.toHaveBeenCalled();
  });

  it("keeps the draft frozen after an uncertain package update and only reads a separate snapshot", async () => {
    const original = packageRecord();
    const get = vi.fn<AdminMembershipApi["getPurchasePackage"]>(async () => original);
    const update = vi.fn<AdminMembershipApi["updatePurchasePackage"]>(async () => {
      throw new ApiError("network", "网络中断");
    });
    render(<PurchasePackages {...packageProps(membershipApi({ getPurchasePackage: get, updatePurchasePackage: update }), {
      mode: "edit", recordId: original.id, viewKey: "packages/edit/unknown",
    })} />);

    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue(original.name));
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "Draft retained" } });
    fireEvent.click(screen.getByRole("button", { name: "保存更改" }));
    expect(await screen.findByRole("region", { name: "套餐写入结果待核对" })).toBeTruthy();
    expect(screen.getByLabelText("名称")).toHaveValue("Draft retained");
    expect(screen.getByRole("button", { name: "保存更改" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "只读读取该套餐" }));
    await screen.findByText(/只读快照/);
    expect(get).toHaveBeenCalledTimes(2);
    expect(update).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("名称")).toHaveValue("Draft retained");
  });

  it("ignores a late authentication failure from a previous package id", async () => {
    const first = deferred<AdminPurchasePackage>();
    const secondRecord = packageRecord({ id: "package-2", name: "Second owner record" });
    const get = vi.fn<AdminMembershipApi["getPurchasePackage"]>()
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce(secondRecord);
    const onAuthenticationError = vi.fn();
    const api = membershipApi({ getPurchasePackage: get });
    const view = render(<PurchasePackages {...packageProps(api, {
      mode: "edit", recordId: "package-1", viewKey: "packages/edit/package-1", onAuthenticationError,
    })} />);

    view.rerender(<PurchasePackages {...packageProps(api, {
      mode: "edit", recordId: "package-2", viewKey: "packages/edit/package-2", onAuthenticationError,
    })} />);
    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("Second owner record"));
    await act(async () => first.reject(new ApiError("http", "旧会话失效", 401, "旧会话失效")));
    expect(onAuthenticationError).not.toHaveBeenCalled();
    expect(screen.getByLabelText("名称")).toHaveValue("Second owner record");
  });
});


describe("package create contract", () => {
  it("rejects an empty price draft and sends zero and false without coercion", async () => {
    const create = vi.fn<AdminMembershipApi["createPurchasePackage"]>(async (input) => ({
      ...packageRecord(), ...input, id: "package-created",
    }));
    render(<PurchasePackages {...packageProps(membershipApi({ createPurchasePackage: create }), {
      mode: "create", viewKey: "packages/create",
    })} />);

    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "New package" } });
    fireEvent.change(screen.getByLabelText("价格（整数元）"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "创建套餐" }));
    expect(await screen.findByText(/价格必须填写整数元/)).toBeTruthy();
    expect(create).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("价格（整数元）"), { target: { value: "0" } });
    fireEvent.click(screen.getByLabelText("启用"));
    const form = screen.getByLabelText("名称").closest("form");
    if (!form) throw new Error("expected package form");
    fireEvent.submit(form);
    fireEvent.submit(form);
    await waitFor(() => expect(create).toHaveBeenCalledWith({
      category: "credits", name: "New package", price: 0, credits: null, days: null, sort_order: 0, is_active: false,
    }, expect.any(AbortSignal)));
    expect(create).toHaveBeenCalledTimes(1);
  });
});


describe("membership owner and pending-write isolation", () => {
  it("allows deletion of a used code and explains that earned rights are not reversed", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const remove = vi.fn<AdminMembershipApi["deleteRedemptionCode"]>(async () => ({ message: "删除成功" }));
    render(<RedemptionCodes {...redemptionProps(membershipApi({
      listRedemptionCodes: vi.fn(async () => ({ data: [code({ is_used: true })], total: 1 })),
      deleteRedemptionCode: remove,
    }))} />);

    await screen.findByText("SYNTHETIC-CODE");
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("删除不会撤销已经兑换的权益"));
    await waitFor(() => expect(remove).toHaveBeenCalledWith("code-1", expect.any(AbortSignal)));
  });

  it("does not repeat a package delete after an unknown result and reads the current record only", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const remove = vi.fn<AdminMembershipApi["deletePurchasePackage"]>(async () => {
      throw new ApiError("network", "网络中断");
    });
    const get = vi.fn<AdminMembershipApi["getPurchasePackage"]>(async () => packageRecord({ is_active: false }));
    render(<PurchasePackages {...packageProps(membershipApi({
      listPurchasePackages: vi.fn(async () => ({ data: [packageRecord()], total: 1 })),
      deletePurchasePackage: remove,
      getPurchasePackage: get,
    }))} />);

    await screen.findByText("Legacy package");
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(await screen.findByLabelText("删除结果待核对")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "只读查询套餐" }));
    await screen.findByText(/套餐当前已停用/);
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(remove).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith("package-1", expect.any(AbortSignal));
  });

  it("drops a late CSV response after the session owner changes", async () => {
    const pending = deferred<{ data: AdminRedemptionCode[]; total: number }>();
    const list = vi.fn<AdminMembershipApi["listRedemptionCodes"]>((query) => query.limit === 1000
      ? pending.promise
      : Promise.resolve({ data: [code()], total: 1 }));
    const createObjectUrl = vi.fn(() => "blob:synthetic");
    const descriptor = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
    try {
      const api = membershipApi({ listRedemptionCodes: list });
      const view = render(<RedemptionCodes {...redemptionProps(api)} />);
      await screen.findByText("SYNTHETIC-CODE");
      fireEvent.click(screen.getByRole("button", { name: /导出 CSV/ }));
      expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 1000 }), expect.any(AbortSignal));

      view.rerender(<RedemptionCodes {...redemptionProps(api, {
        ownerScope: "admin-1/session-2", viewKey: "redemption/list/session-2",
      })} />);
      await act(async () => pending.resolve({ data: [code()], total: 1 }));
      expect(createObjectUrl).not.toHaveBeenCalled();
    } finally {
      if (descriptor) Object.defineProperty(URL, "createObjectURL", descriptor);
      else delete (URL as unknown as { createObjectURL?: unknown }).createObjectURL;
    }
  });

  it("keeps the new package id after an old successful read arrives", async () => {
    const oldRead = deferred<AdminPurchasePackage>();
    const current = packageRecord({ id: "package-2", name: "Current record" });
    const get = vi.fn<AdminMembershipApi["getPurchasePackage"]>()
      .mockReturnValueOnce(oldRead.promise)
      .mockResolvedValueOnce(current);
    const api = membershipApi({ getPurchasePackage: get });
    const view = render(<PurchasePackages {...packageProps(api, {
      mode: "edit", recordId: "package-1", viewKey: "packages/edit/one",
    })} />);

    view.rerender(<PurchasePackages {...packageProps(api, {
      mode: "edit", recordId: "package-2", viewKey: "packages/edit/two",
    })} />);
    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("Current record"));
    await act(async () => oldRead.resolve(packageRecord({ id: "package-1", name: "Stale record" })));
    expect(screen.getByLabelText("名称")).toHaveValue("Current record");
  });
});


describe("session-switch in-flight isolation", () => {
  it("releases the old owner's lock and prevents its late finally or success from touching a new create draft", async () => {
    const oldRequest = deferred<AdminPurchasePackage>();
    const newRequest = deferred<AdminPurchasePackage>();
    const create = vi.fn<AdminMembershipApi["createPurchasePackage"]>()
      .mockReturnValueOnce(oldRequest.promise)
      .mockReturnValueOnce(newRequest.promise);
    const api = membershipApi({ createPurchasePackage: create });
    const props = packageProps(api, { mode: "create", viewKey: "packages/create/one" });
    const view = render(<PurchasePackages {...props} />);

    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "Old draft" } });
    fireEvent.click(screen.getByRole("button", { name: "创建套餐" }));
    expect(create).toHaveBeenCalledTimes(1);

    view.rerender(<PurchasePackages {...packageProps(api, {
      mode: "create", ownerScope: "admin-1/session-2", viewKey: "packages/create/two",
    })} />);
    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue(""));
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "New draft" } });
    fireEvent.click(screen.getByRole("button", { name: "创建套餐" }));
    expect(create).toHaveBeenCalledTimes(2);

    await act(async () => oldRequest.resolve(packageRecord({ id: "old", name: "Old draft" })));
    expect(screen.getByLabelText("名称")).toHaveValue("New draft");
    expect(screen.getByRole("button", { name: "提交中…" })).toBeDisabled();

    await act(async () => newRequest.resolve(packageRecord({ id: "new", name: "New draft" })));
    expect(await screen.findByText(/New draft · credits/)).toBeTruthy();
  });
});



describe("membership delete and unknown-check lifecycle regressions", () => {
  it("does not roll a redemption list back to a negative skip after an old-query delete", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const deletion = deferred<{ message: string }>();
    const target = code({ id: "code-page-two", code: "PAGE-TWO" });
    const list = vi.fn<AdminMembershipApi["listRedemptionCodes"]>(async (query) => (
      query.skip === 25
        ? { data: [target], total: 26 }
        : { data: Array.from({ length: 10 }, (_, index) => code({ id: "sort-" + index, code: "SORT-" + index })), total: 26 }
    ));
    const api = membershipApi({
      listRedemptionCodes: list,
      deleteRedemptionCode: vi.fn(() => deletion.promise),
    });
    render(<RedemptionCodes {...redemptionProps(api)} />);

    await screen.findByText("SORT-0");
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("PAGE-TWO");
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(await screen.findByText("处理中…")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "按ID排序" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(
      expect.objectContaining({ skip: 0, sort_field: "id" }), expect.any(AbortSignal),
    ));

    await act(async () => deletion.resolve({ message: "删除成功" }));
    await screen.findByText("删除成功");
    expect(list.mock.calls.every(([query]) => query.skip >= 0)).toBe(true);
  });

  it("does not roll a package list back to a negative skip after an old-query delete", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const deletion = deferred<{ message: string }>();
    const target = packageRecord({ id: "package-page-two", name: "Page two" });
    const list = vi.fn<AdminMembershipApi["listPurchasePackages"]>(async (query) => (
      query.skip === 10
        ? { data: [target], total: 11 }
        : { data: Array.from({ length: 10 }, (_, index) => packageRecord({ id: "sort-" + index, name: "Sort " + index })), total: 11 }
    ));
    const api = membershipApi({
      listPurchasePackages: list,
      deletePurchasePackage: vi.fn(() => deletion.promise),
    });
    render(<PurchasePackages {...packageProps(api)} />);

    await screen.findByText("Sort 0");
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("Page two");
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(await screen.findByText("处理中…")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "按ID排序" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(
      expect.objectContaining({ skip: 0, sort_field: "id" }), expect.any(AbortSignal),
    ));

    await act(async () => deletion.resolve({ message: "删除成功" }));
    await screen.findByText("删除成功");
    expect(list.mock.calls.every(([query]) => query.skip >= 0)).toBe(true);
  });

  it.each([
    ["late 401", new ApiError("http", "过期", 401, "过期")],
    ["late 403", new ApiError("http", "拒绝", 403, "拒绝")],
  ])("ignores %s from an aborted package-list unknown check after the view changes", async (_label, failure) => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const check = deferred<AdminPurchasePackage>();
    const onAuthenticationError = vi.fn();
    const api = membershipApi({
      listPurchasePackages: vi.fn(async () => ({ data: [packageRecord()], total: 1 })),
      deletePurchasePackage: vi.fn(async () => { throw new ApiError("network", "网络中断"); }),
      getPurchasePackage: vi.fn(() => check.promise),
    });
    const view = render(<PurchasePackages {...packageProps(api, { onAuthenticationError })} />);

    await screen.findByText("Legacy package");
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    await screen.findByLabelText("删除结果待核对");
    fireEvent.click(screen.getByRole("button", { name: "只读查询套餐" }));
    view.rerender(<PurchasePackages {...packageProps(api, {
      onAuthenticationError, viewKey: "packages/list/next",
    })} />);
    await screen.findByRole("table", { name: "购买套餐列表" });

    await act(async () => { check.reject(failure); });
    expect(onAuthenticationError).not.toHaveBeenCalled();
  });
});
