import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../shared/api/errors";
import { UserRelatedRecords } from "./UserRelatedRecords";
import { downloadAdminUsersCsv, serializeAdminUsersCsv } from "./userExport";
import type {
  AdminUserCreateInput,
  AdminUserListQuery,
  AdminUserRecord,
  AdminUserRelatedResource,
  AdminUserSortField,
  AdminUserUpdateInput,
  AdminUsersApi,
} from "./usersTypes";
import "./Users.css";

const PAGE_SIZE = 10;

export type AdminUsersViewMode = "list" | "create" | "edit";

export interface UsersProps {
  api: AdminUsersApi;
  onAuthenticationError(): void;
  mode: AdminUsersViewMode;
  userId?: string | undefined;
  currentAdministratorId: string;
  listVersion?: number;
  onCreate(): void;
  onEdit(userId: string): void;
  onCancel(): void;
  onSaved(): void;
}

type PageStatus = "loading" | "ready" | "error";

interface UserFilters {
  username: string;
  email: string;
  membership_type: string;
  is_superuser: string;
}

const EMPTY_FILTERS: UserFilters = {
  username: "",
  email: "",
  membership_type: "",
  is_superuser: "",
};

interface UserDraft {
  username: string;
  email: string;
  password: string;
  membership_type: string;
  membership_expires_at: string;
  membership_expires_at_original: string | null;
  membership_expires_at_changed: boolean;
  is_superuser: boolean;
  credits: string;
}

function emptyDraft(): UserDraft {
  return {
    username: "",
    email: "",
    password: "",
    membership_type: "free",
    membership_expires_at: "",
    membership_expires_at_original: null,
    membership_expires_at_changed: false,
    is_superuser: false,
    credits: "0",
  };
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.kind === "network") return "无法连接管理后台服务，请检查服务后重试。";
    if (error.kind === "timeout") return "请求超时，请确认服务状态后重试。";
    if (error.kind === "invalid-response") return error.message;
    return error.detail ?? error.message;
  }
  return "请求失败，请重试。";
}

function handleAuthenticationError(error: unknown, callback: () => void): boolean {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    callback();
    return true;
  }
  return false;
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function membershipLabel(user: AdminUserRecord): string {
  if (user.membership_type === "free") return "普通";
  return user.membership_type === "premium" ? "会员" : user.membership_type;
}

function queryFromFilters(filters: UserFilters): Pick<AdminUserListQuery, "username" | "email" | "membership_type" | "is_superuser"> {
  return {
    ...(filters.username !== "" ? { username: filters.username } : {}),
    ...(filters.email !== "" ? { email: filters.email } : {}),
    ...(filters.membership_type !== "" ? { membership_type: filters.membership_type } : {}),
    ...(filters.is_superuser !== "" ? { is_superuser: filters.is_superuser === "true" } : {}),
  };
}

interface SortHeaderProps {
  field: AdminUserSortField;
  label: string;
  activeField: AdminUserSortField;
  order: "asc" | "desc";
  onSort(field: AdminUserSortField): void;
}

function SortHeader({ field, label, activeField, order, onSort }: SortHeaderProps) {
  const active = field === activeField;
  return (
    <th scope="col" aria-sort={active ? (order === "asc" ? "ascending" : "descending") : "none"}>
      <button
        className="admin-users-sort-button"
        type="button"
        onClick={() => onSort(field)}
        aria-label={"按" + label + "排序"}
      >
        {label}
        <span aria-hidden="true">{active ? (order === "asc" ? " ↑" : " ↓") : ""}</span>
      </button>
    </th>
  );
}

interface UserListProps {
  api: AdminUsersApi;
  onAuthenticationError(): void;
  currentAdministratorId: string;
  onCreate(): void;
  onEdit(userId: string): void;
  listVersion: number;
}

function UserList({ api, onAuthenticationError, currentAdministratorId, onCreate, onEdit, listVersion }: UserListProps) {
  const [draftFilters, setDraftFilters] = useState<UserFilters>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const [sortField, setSortField] = useState<AdminUserSortField>("id");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [rows, setRows] = useState<AdminUserRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<PageStatus>("loading");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [deletingUserId, setDeletingUserId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteNotice, setDeleteNotice] = useState<string | null>(null);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportNotice, setExportNotice] = useState<string | null>(null);
  const deleteInFlight = useRef(false);
  const mounted = useRef(false);
  const exportController = useRef<AbortController | null>(null);
  const exportSequence = useRef(0);

  const query = useMemo<AdminUserListQuery>(() => ({
    skip: (page - 1) * PAGE_SIZE,
    limit: PAGE_SIZE,
    sort_field: sortField,
    sort_order: sortOrder,
    ...queryFromFilters(draftFilters),
  }), [draftFilters, page, sortField, sortOrder]);
  const currentQuery = useRef(query);
  currentQuery.current = query;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      exportSequence.current += 1;
      exportController.current?.abort();
      exportController.current = null;
    };
  }, []);

  useEffect(() => {
    exportController.current?.abort();
    exportController.current = null;
    exportSequence.current += 1;
    setExportBusy(false);
    setExportError(null);
    setExportNotice(null);
  }, [query]);

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setStatus("loading");
    setLoadError(null);
    setRows([]);

    void api.listUsers(query, controller.signal).then((result) => {
      if (!current) return;
      setRows(result.data);
      setTotal(result.total);
      setStatus("ready");
    }).catch((error: unknown) => {
      if (!current || controller.signal.aborted) return;
      if (handleAuthenticationError(error, onAuthenticationError)) return;
      setLoadError(errorMessage(error));
      setStatus("error");
    });

    return () => {
      current = false;
      controller.abort();
    };
  }, [api, onAuthenticationError, query, listVersion, retry]);

  async function exportUsers() {
    if (exportBusy) return;
    const requestQuery = query;
    const controller = new AbortController();
    const sequence = exportSequence.current + 1;
    exportSequence.current = sequence;
    exportController.current = controller;
    setExportBusy(true);
    setExportError(null);
    setExportNotice(null);

    try {
      const result = await api.listUsers({ ...requestQuery, skip: 0, limit: 1000 }, controller.signal);
      if (controller.signal.aborted || currentQuery.current !== requestQuery || exportSequence.current !== sequence) return;
      const records = result.data.slice(0, 1000);
      downloadAdminUsersCsv(
        serializeAdminUsersCsv(records),
        "haoai-users-" + new Date().toISOString().slice(0, 10) + ".csv",
      );
      setExportNotice(result.total > records.length
        ? `已导出前 ${records.length} 条，共有 ${result.total} 条符合当前筛选。`
        : `已导出 ${records.length} 条用户记录。`);
    } catch (cause) {
      if (controller.signal.aborted || currentQuery.current !== requestQuery || exportSequence.current !== sequence) return;
      if (!handleAuthenticationError(cause, onAuthenticationError)) {
        setExportError(errorMessage(cause));
      }
    } finally {
      if (mounted.current && exportSequence.current === sequence) {
        exportController.current = null;
        setExportBusy(false);
      }
    }
  }

  async function deleteUser(user: AdminUserRecord) {
    if (user.id === currentAdministratorId || deleteInFlight.current) return;
    const confirmed = window.confirm(
      `确定删除用户“${user.username}”（${user.id}）吗？系统将删除该用户以及目前支持级联删除的积分流水、模型与提示词配置、剧集等关联数据，此操作无法撤销。`,
    );
    if (!confirmed || deleteInFlight.current) return;

    const requestQuery = query;
    deleteInFlight.current = true;
    setDeletingUserId(user.id);
    setDeleteError(null);
    setDeleteNotice(null);
    try {
      const result = await api.deleteUser(user.id);
      if (!mounted.current) return;
      setDeleteNotice(result.message);
      if (currentQuery.current === requestQuery && rows.length === 1 && page > 1) {
        setPage((value) => value - 1);
      } else {
        setRetry((value) => value + 1);
      }
    } catch (cause) {
      if (!mounted.current) return;
      if (!handleAuthenticationError(cause, onAuthenticationError)) {
        setDeleteError(`${errorMessage(cause)} 删除结果未确认，列表保持原样；请重新查询核对后再决定是否重试。系统不会自动重发删除请求。`);
      }
    } finally {
      deleteInFlight.current = false;
      if (mounted.current) setDeletingUserId(null);
    }
  }

  function changeFilter<K extends keyof UserFilters>(key: K, value: UserFilters[K]) {
    setDraftFilters((current) => ({ ...current, [key]: value }));
    setPage(1);
  }

  function resetFilters() {
    setDraftFilters(EMPTY_FILTERS);
    setPage(1);
  }

  function sortBy(field: AdminUserSortField) {
    if (sortField === field) {
      setSortOrder((value) => value === "asc" ? "desc" : "asc");
    } else {
      setSortField(field);
      setSortOrder("asc");
    }
    setPage(1);
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="admin-users-page">
      <header className="admin-page-heading">
        <div>
          <p className="admin-eyebrow">账号管理</p>
          <h1>用户管理</h1>
          <p>查看用户资料、会员状态和管理员设置。</p>
        </div>
        <div className="admin-users-heading-actions">
          <button className="admin-button admin-button-secondary" type="button" onClick={() => { void exportUsers(); }} disabled={exportBusy}>
            {exportBusy ? "导出中…" : "导出 CSV（最多 1000 条）"}
          </button>
          <button className="admin-button admin-button-primary" type="button" onClick={onCreate}>
            新建用户
          </button>
        </div>
      </header>

      <section className="admin-filter-panel admin-users-filters" aria-label="用户筛选">
        <label>
          <span>用户名</span>
          <input value={draftFilters.username} onChange={(event) => changeFilter("username", event.target.value)} />
        </label>
        <label>
          <span>邮箱</span>
          <input value={draftFilters.email} onChange={(event) => changeFilter("email", event.target.value)} />
        </label>
        <label>
          <span>会员</span>
          <select value={draftFilters.membership_type} onChange={(event) => changeFilter("membership_type", event.target.value)}>
            <option value="">全部</option>
            <option value="free">普通</option>
            <option value="premium">会员</option>
          </select>
        </label>
        <label>
          <span>管理员</span>
          <select value={draftFilters.is_superuser} onChange={(event) => changeFilter("is_superuser", event.target.value)}>
            <option value="">全部</option>
            <option value="true">是</option>
            <option value="false">否</option>
          </select>
        </label>
        <div className="admin-users-filter-actions">
          <button className="admin-button admin-button-secondary" type="button" onClick={resetFilters}>重置</button>
        </div>
        <span className="admin-sort-note">按{sortField === "created_at" ? "注册时间" : sortField === "id" ? "用户 ID" : sortField} {sortOrder === "asc" ? "升序" : "降序"}</span>
      </section>

      <div className="admin-users-feedback" aria-live="polite">
        {exportError ? <p className="admin-form-error" role="alert">CSV 导出失败：{exportError}</p> : null}
        {exportNotice ? <p className="admin-users-notice" role="status">{exportNotice}</p> : null}
        {deleteError ? (
          <section className="admin-users-delete-error" role="alert">
            <p>删除未完成：{deleteError}</p>
            <button className="admin-button admin-button-secondary" type="button" onClick={() => {
              setDeleteError(null);
              setDeleteNotice(null);
              setRetry((value) => value + 1);
            }}>重新查询用户列表</button>
          </section>
        ) : null}
        {deleteNotice ? <p className="admin-users-notice" role="status">{deleteNotice}，正在刷新用户列表。</p> : null}
      </div>

      {status === "loading" ? (
        <section className="admin-state-card" role="status">正在读取用户…</section>
      ) : status === "error" ? (
        <section className="admin-state-card" role="alert">
          <p>{loadError}</p>
          <button className="admin-button" type="button" onClick={() => setRetry((value) => value + 1)}>重试</button>
        </section>
      ) : (
        <div className="admin-table-wrap admin-users-table-wrap">
          {rows.length === 0 ? (
            <section className="admin-state-card admin-users-empty">
              <strong>没有符合条件的用户</strong>
              <p>{total === 0 ? "调整筛选条件，或新建一个用户。" : "当前页没有用户记录。"}</p>
            </section>
          ) : (
            <table className="admin-users-table" aria-label="用户列表">
              <thead>
                <tr>
                  <SortHeader field="username" label="用户名" activeField={sortField} order={sortOrder} onSort={sortBy} />
                  <SortHeader field="id" label="用户 ID" activeField={sortField} order={sortOrder} onSort={sortBy} />
                  <SortHeader field="email" label="邮箱" activeField={sortField} order={sortOrder} onSort={sortBy} />
                  <SortHeader field="membership_type" label="会员" activeField={sortField} order={sortOrder} onSort={sortBy} />
                  <SortHeader field="membership_expires_at" label="会员到期" activeField={sortField} order={sortOrder} onSort={sortBy} />
                  <SortHeader field="is_superuser" label="管理员" activeField={sortField} order={sortOrder} onSort={sortBy} />
                  <SortHeader field="credits" label="积分" activeField={sortField} order={sortOrder} onSort={sortBy} />
                  <SortHeader field="created_at" label="注册时间" activeField={sortField} order={sortOrder} onSort={sortBy} />
                  <th scope="col"><span className="admin-visually-hidden">操作</span></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((user) => {
                  const membership = membershipLabel(user);
                  return (
                    <tr key={user.id}>
                      <td>
                        <button className="admin-table-link" type="button" onClick={() => onEdit(user.id)}>{user.username}</button>
                      </td>
                      <td className="admin-users-id">{user.id}</td>
                      <td>{user.email}</td>
                      <td>
                        <span className="admin-users-membership">{membership}</span>
                      </td>
                      <td>{formatDate(user.membership_expires_at)}</td>
                      <td>{user.is_superuser ? "是" : "否"}</td>
                      <td>{user.credits.toLocaleString("zh-CN")}</td>
                      <td>{formatDate(user.created_at)}</td>
                      <td className="admin-users-row-actions">
                        <button className="admin-text-button" type="button" onClick={() => onEdit(user.id)}>编辑</button>
                        <button
                          className="admin-text-button admin-users-delete-button"
                          type="button"
                          disabled={deletingUserId !== null || user.id === currentAdministratorId}
                          title={user.id === currentAdministratorId ? "不能删除当前登录的管理员账号" : ""}
                          aria-label={user.id === currentAdministratorId ? "删除当前管理员不可用" : "删除用户 " + user.username}
                          onClick={() => { void deleteUser(user); }}
                        >
                          {user.id === currentAdministratorId ? "当前管理员" : deletingUserId === user.id ? "删除中…" : "删除"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          <footer className="admin-table-footer">
            <span>共 {total.toLocaleString("zh-CN")} 个用户</span>
            <div className="admin-pagination">
              <button
                className="admin-button admin-button-secondary"
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((value) => Math.max(1, value - 1))}
              >上一页</button>
              <span>第 {page} / {pageCount} 页</span>
              <button
                className="admin-button admin-button-secondary"
                type="button"
                disabled={page >= pageCount}
                onClick={() => setPage((value) => Math.min(pageCount, value + 1))}
              >下一页</button>
            </div>
          </footer>
        </div>
      )}
    </section>
  );
}

function toLocalDateTime(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  const pad = (part: number) => String(part).padStart(2, "0");
  return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate())
    + "T" + pad(date.getHours()) + ":" + pad(date.getMinutes());
}

function membershipExpiryPayload(draft: UserDraft, mode: "create" | "edit"): string | null {
  if (mode === "edit" && !draft.membership_expires_at_changed) {
    return draft.membership_expires_at_original;
  }
  return draft.membership_expires_at === "" ? null : draft.membership_expires_at;
}

function userDraftFromRecord(user: AdminUserRecord): UserDraft {
  return {
    username: user.username,
    email: user.email,
    password: "",
    membership_type: user.membership_type,
    membership_expires_at: toLocalDateTime(user.membership_expires_at),
    membership_expires_at_original: user.membership_expires_at,
    membership_expires_at_changed: false,
    is_superuser: user.is_superuser,
    credits: String(user.credits),
  };
}

interface UserFormProps {
  api: AdminUsersApi;
  onAuthenticationError(): void;
  mode: Exclude<AdminUsersViewMode, "list">;
  userId?: string | undefined;
  onCancel(): void;
  onSaved(): void;
}

function UserForm({ api, onAuthenticationError, mode, userId, onCancel, onSaved }: UserFormProps) {
  const [draft, setDraft] = useState<UserDraft>(emptyDraft);
  const [loadStatus, setLoadStatus] = useState<PageStatus>(mode === "edit" ? "loading" : "ready");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [retry, setRetry] = useState(0);
  const [activeTab, setActiveTab] = useState<"basic" | AdminUserRelatedResource>("basic");
  const mounted = useRef(false);
  const saveController = useRef<AbortController | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      saveController.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (mode === "create") {
      setDraft(emptyDraft());
      setLoadError(null);
      setLoadStatus("ready");
      return;
    }
    if (!userId) {
      setLoadError("缺少用户编号，无法读取用户资料。");
      setLoadStatus("error");
      return;
    }

    const controller = new AbortController();
    let current = true;
    setLoadStatus("loading");
    setLoadError(null);
    setSaveError(null);

    void api.getUser(userId, controller.signal).then((user) => {
      if (current) setDraft(userDraftFromRecord(user));
    }).catch((error: unknown) => {
      if (!current || controller.signal.aborted) return;
      if (handleAuthenticationError(error, onAuthenticationError)) return;
      setLoadError(errorMessage(error));
    }).finally(() => {
      if (current) setLoadStatus((value) => value === "error" ? value : "ready");
    });

    return () => {
      current = false;
      controller.abort();
    };
  }, [api, mode, userId, retry, onAuthenticationError]);

  function change<K extends keyof UserDraft>(key: K, value: UserDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || (mode === "edit" && !userId)) return;
    const credits = draft.credits.trim() === "" ? null : Number(draft.credits);
    if (credits !== null && !Number.isInteger(credits)) {
      setSaveError("积分必须是整数。");
      return;
    }

    setSaving(true);
    setSaveError(null);
    const controller = new AbortController();
    saveController.current = controller;
    try {
      const common = {
        username: draft.username,
        email: draft.email,
        is_superuser: draft.is_superuser,
        membership_type: draft.membership_type,
        membership_expires_at: membershipExpiryPayload(draft, mode),
      };
      if (mode === "create") {
        const input: AdminUserCreateInput = { ...common, password: draft.password, credits: credits ?? 0 };
        await api.createUser(input, controller.signal);
      } else {
        const input: AdminUserUpdateInput = { ...common, credits };
        await api.updateUser(userId as string, input, controller.signal);
      }
      if (mounted.current && !controller.signal.aborted) onSaved();
    } catch (error) {
      if (!mounted.current || controller.signal.aborted) return;
      if (!handleAuthenticationError(error, onAuthenticationError)) {
        setSaveError(errorMessage(error));
      }
    } finally {
      if (mounted.current && saveController.current === controller) {
        saveController.current = null;
        setSaving(false);
      }
    }
  }

  if (loadStatus === "loading") {
    return <section className="admin-state-card" role="status">正在读取用户资料…</section>;
  }
  if (loadError) {
    return (
      <section className="admin-state-card" role="alert">
        <p>{loadError}</p>
        <div className="admin-button-row">
          <button className="admin-button admin-button-secondary" type="button" onClick={onCancel}>返回用户列表</button>
          {userId ? <button className="admin-button" type="button" onClick={() => setRetry((value) => value + 1)}>重试读取</button> : null}
        </div>
      </section>
    );
  }

  return (
    <section className="admin-users-page">
      <header className="admin-page-heading">
        <div>
          <p className="admin-eyebrow">账号管理</p>
          <h1>{mode === "create" ? "新建用户" : "编辑用户"}</h1>
          <p>{mode === "create" ? "填写账号、会员和初始积分信息。" : "更新账号、会员和积分信息。留空的到期时间表示永久有效。"}</p>
        </div>
      </header>

      {mode === "edit" && userId ? (
        <nav className="admin-user-tabs" role="tablist" aria-label="用户资料页签">
          <button type="button" role="tab" aria-selected={activeTab === "basic"} onClick={() => setActiveTab("basic")}>基本信息</button>
          <button type="button" role="tab" aria-selected={activeTab === "credit-logs"} onClick={() => setActiveTab("credit-logs")}>积分流水</button>
          <button type="button" role="tab" aria-selected={activeTab === "model-configs"} onClick={() => setActiveTab("model-configs")}>模型配置</button>
          <button type="button" role="tab" aria-selected={activeTab === "prompt-configs"} onClick={() => setActiveTab("prompt-configs")}>提示词配置</button>
          <button type="button" role="tab" aria-selected={activeTab === "tasks"} onClick={() => setActiveTab("tasks")}>AI 任务</button>
          <button type="button" role="tab" aria-selected={activeTab === "series"} onClick={() => setActiveTab("series")}>剧集</button>
        </nav>
      ) : null}

      <form className="admin-model-form admin-users-form" hidden={mode === "edit" && activeTab !== "basic"} onSubmit={(event) => { void submit(event); }}>
        <div className="admin-form-grid">
          <label className="admin-field">
            <span>用户名</span>
            <input required value={draft.username} onChange={(event) => change("username", event.target.value)} autoComplete="off" />
          </label>
          <label className="admin-field">
            <span>邮箱</span>
            <input required type="email" value={draft.email} onChange={(event) => change("email", event.target.value)} autoComplete="email" />
          </label>
          {mode === "create" ? (
            <label className="admin-field admin-field-span">
              <span>初始密码</span>
              <input required type="password" value={draft.password} onChange={(event) => change("password", event.target.value)} autoComplete="new-password" />
            </label>
          ) : null}
          <label className="admin-field">
            <span>会员类型</span>
            <select value={draft.membership_type} onChange={(event) => change("membership_type", event.target.value)}>
              <option value="free">普通用户</option>
              <option value="premium">会员</option>
              {draft.membership_type !== "free" && draft.membership_type !== "premium" ? (
                <option value={draft.membership_type}>
                  {draft.membership_type === "" ? "当前值（空）" : draft.membership_type + "（当前值）"}
                </option>
              ) : null}
            </select>
          </label>
          <label className="admin-field">
            <span>会员到期时间（留空=永久有效）</span>
            <input
              type="datetime-local"
              value={draft.membership_expires_at}
              onChange={(event) => setDraft((current) => ({
                ...current,
                membership_expires_at: event.target.value,
                membership_expires_at_changed: true,
              }))}
            />
          </label>
          <label className="admin-field">
            <span>{mode === "create" ? "初始积分" : "积分"}</span>
            <input type="number" step="1" value={draft.credits} onChange={(event) => change("credits", event.target.value)} />
          </label>
          <label className="admin-checkbox-field admin-users-admin-field">
            <input type="checkbox" checked={draft.is_superuser} onChange={(event) => change("is_superuser", event.target.checked)} />
            <span>超级管理员</span>
          </label>
        </div>

        {saveError ? <p className="admin-form-error" role="alert">{saveError}</p> : null}
        <div className="admin-form-actions">
          <button className="admin-button admin-button-secondary" type="button" onClick={onCancel} disabled={saving}>取消</button>
          <button className="admin-button admin-button-primary" type="submit" disabled={saving}>
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </form>

      {mode === "edit" && userId && activeTab !== "basic" ? (
        <UserRelatedRecords
          key={activeTab}
          api={api}
          resource={activeTab}
          userId={userId}
          onAuthenticationError={onAuthenticationError}
        />
      ) : null}
    </section>
  );
}

export function Users(props: UsersProps) {
  if (props.mode === "list") {
    return (
      <UserList
        api={props.api}
        onAuthenticationError={props.onAuthenticationError}
        currentAdministratorId={props.currentAdministratorId}
        onCreate={props.onCreate}
        onEdit={props.onEdit}
        listVersion={props.listVersion ?? 0}
      />
    );
  }
  return (
    <UserForm
      api={props.api}
      onAuthenticationError={props.onAuthenticationError}
      key={props.mode === "edit" ? "edit:" + (props.userId ?? "") : "create"}
      mode={props.mode}
      userId={props.userId}
      onCancel={props.onCancel}
      onSaved={props.onSaved}
    />
  );
}
