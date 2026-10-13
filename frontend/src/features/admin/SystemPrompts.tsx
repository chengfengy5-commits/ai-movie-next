import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../shared/api/errors";
import type {
  SystemPrompt,
  SystemPromptInput,
  SystemPromptQuery,
  SystemPromptsApi,
} from "./systemPromptsTypes";
import "./SystemPrompts.css";

const PAGE_SIZE = 10;

const CATEGORIES = [
  { value: "storyboard", label: "一键分镜" },
  { value: "scene-segmentation", label: "章节拆分" },
  { value: "character-extraction", label: "角色提取" },
  { value: "scene-extraction", label: "场景提取" },
  { value: "prop-extraction", label: "道具提取" },
  { value: "character-design", label: "角色设计图" },
  { value: "scene-design", label: "场景概念图" },
  { value: "prop-design", label: "道具设计图" },
  { value: "storyboard-design", label: "故事板设计图" },
  { value: "chat-assistant", label: "AI聊天模式" },
  { value: "video-design", label: "视频生成" },
  { value: "image", label: "图片生成" },
  { value: "frame-optimize", label: "分镜优化" },
  { value: "ai-review", label: "AI复核" },
  { value: "style", label: "风格" },
] as const;

type SystemPromptsMode = "list" | "create" | "edit";

export interface SystemPromptsProps {
  api: SystemPromptsApi;
  onAuthenticationError(): void;
  mode: SystemPromptsMode;
  promptId?: string | undefined;
  reloadVersion: number;
  onCreate?(): void;
  onEdit?(id: string): void;
  onCancel?(): void;
  onSaved?(): void;
}

type LoadStatus = "loading" | "ready" | "error";

interface PromptDraft {
  category: string;
  name: string;
  system_prompt: string;
  user_prompt: string;
  sort_order: number;
  is_active: boolean;
}

function emptyDraft(): PromptDraft {
  return {
    category: "",
    name: "",
    system_prompt: "",
    user_prompt: "",
    sort_order: 0,
    is_active: true,
  };
}

function draftFromPrompt(prompt: SystemPrompt): PromptDraft {
  return {
    category: prompt.category,
    name: prompt.name,
    system_prompt: prompt.system_prompt ?? "",
    user_prompt: prompt.user_prompt ?? "",
    sort_order: prompt.sort_order,
    is_active: prompt.is_active,
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

function handleAuthenticationError(
  error: unknown,
  onAuthenticationError: () => void,
): boolean {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    onAuthenticationError();
    return true;
  }
  return false;
}

interface PromptListProps {
  api: SystemPromptsApi;
  onAuthenticationError(): void;
  onCreate: (() => void) | undefined;
  onEdit: ((id: string) => void) | undefined;
  reloadVersion: number;
}

function PromptList({
  api,
  onAuthenticationError,
  onCreate,
  onEdit,
  reloadVersion,
}: PromptListProps) {
  const [category, setCategory] = useState("");
  const [active, setActive] = useState("");
  const [page, setPage] = useState(1);
  const [retry, setRetry] = useState(0);
  const [rows, setRows] = useState<SystemPrompt[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<LoadStatus>("loading");
  const [loadError, setLoadError] = useState<string | null>(null);

  const query = useMemo<SystemPromptQuery>(() => {
    const result: SystemPromptQuery = {
      skip: (page - 1) * PAGE_SIZE,
      limit: PAGE_SIZE,
    };
    if (category) result.category = category;
    if (active !== "") result.is_active = active === "true";
    return result;
  }, [active, category, page]);

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setStatus("loading");
    setLoadError(null);
    setRows([]);

    void api.list(query, controller.signal).then((result) => {
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
  }, [api, onAuthenticationError, query, reloadVersion, retry]);

  useEffect(() => {
    const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (page > pageCount) setPage(pageCount);
  }, [page, total]);

  function resetFilters() {
    setCategory("");
    setActive("");
    setPage(1);
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="admin-prompts-page">
      <header className="admin-prompts-heading">
        <div>
          <p className="admin-prompts-eyebrow">内容配置</p>
          <h1>系统提示词</h1>
          <p>管理平台各项 AI 功能使用的系统提示词。</p>
        </div>
        <button className="admin-button admin-button-primary" type="button" onClick={onCreate}>
          新建提示词
        </button>
      </header>

      <section className="admin-prompts-filters" aria-label="提示词筛选">
        <label>
          <span>分类</span>
          <select
            value={category}
            onChange={(event) => {
              setCategory(event.target.value);
              setPage(1);
            }}
          >
            <option value="">全部分类</option>
            {CATEGORIES.map((choice) => (
              <option key={choice.value} value={choice.value}>{choice.label}</option>
            ))}
          </select>
        </label>
        <label>
          <span>启用</span>
          <select
            value={active}
            onChange={(event) => {
              setActive(event.target.value);
              setPage(1);
            }}
          >
            <option value="">全部</option>
            <option value="true">启用</option>
            <option value="false">禁用</option>
          </select>
        </label>
        <button className="admin-button admin-button-secondary" type="button" onClick={resetFilters}>
          重置
        </button>
        <span className="admin-prompts-sort-note">按排序权重升序</span>
      </section>

      {status === "loading" ? (
        <section className="admin-prompts-state" role="status">正在读取提示词…</section>
      ) : status === "error" ? (
        <section className="admin-prompts-state" role="alert">
          <p>{loadError}</p>
          <button className="admin-button" type="button" onClick={() => setRetry((value) => value + 1)}>
            重试
          </button>
        </section>
      ) : rows.length === 0 ? (
        <div className="admin-prompts-table-wrap admin-prompts-empty">
          <section className="admin-prompts-state">
            <strong>没有符合条件的提示词</strong>
            <p>{total === 0 ? "调整筛选条件，或新建一个系统提示词。" : "当前页没有提示词记录。"}</p>
          </section>
          <PromptPagination total={total} page={page} pageCount={pageCount} onPageChange={setPage} />
        </div>
      ) : (
        <div className="admin-prompts-table-wrap">
          <table className="admin-prompts-table">
            <thead>
              <tr>
                <th scope="col">名称</th>
                <th scope="col">分类</th>
                <th scope="col">排序</th>
                <th scope="col">启用</th>
                <th scope="col"><span className="admin-prompts-visually-hidden">操作</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((prompt) => (
                <tr key={prompt.id}>
                  <td>
                    <button
                      className="admin-prompts-row-link"
                      type="button"
                      onClick={() => onEdit?.(prompt.id)}
                    >
                      {prompt.name}
                    </button>
                  </td>
                  <td>{CATEGORIES.find((choice) => choice.value === prompt.category)?.label ?? prompt.category}</td>
                  <td>{prompt.sort_order}</td>
                  <td>
                    <span className={prompt.is_active ? "admin-prompts-status admin-prompts-status-active" : "admin-prompts-status"}>
                      {prompt.is_active ? "启用" : "禁用"}
                    </span>
                  </td>
                  <td className="admin-prompts-actions">
                    <button className="admin-prompts-text-button" type="button" onClick={() => onEdit?.(prompt.id)}>
                      编辑
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <PromptPagination total={total} page={page} pageCount={pageCount} onPageChange={setPage} />
        </div>
      )}
    </section>
  );
}

function PromptPagination({
  total,
  page,
  pageCount,
  onPageChange,
}: {
  total: number;
  page: number;
  pageCount: number;
  onPageChange(page: number): void;
}) {
  return (
    <footer className="admin-prompts-pagination">
      <span>共 {total.toLocaleString("zh-CN")} 个提示词</span>
      <div>
        <button
          className="admin-button admin-button-secondary"
          type="button"
          disabled={page <= 1}
          onClick={() => onPageChange(Math.max(1, page - 1))}
        >
          上一页
        </button>
        <span>第 {page} / {pageCount} 页</span>
        <button
          className="admin-button admin-button-secondary"
          type="button"
          disabled={page >= pageCount}
          onClick={() => onPageChange(Math.min(pageCount, page + 1))}
        >
          下一页
        </button>
      </div>
    </footer>
  );
}

function PromptForm({
  api,
  onAuthenticationError,
  mode,
  promptId,
  onCancel,
  onSaved,
}: {
  api: SystemPromptsApi;
  onAuthenticationError(): void;
  mode: "create" | "edit";
  promptId: string | undefined;
  onCancel: (() => void) | undefined;
  onSaved: (() => void) | undefined;
}) {
  const [draft, setDraft] = useState<PromptDraft>(emptyDraft);
  const [loading, setLoading] = useState(mode === "edit");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [retry, setRetry] = useState(0);
  const mounted = useRef(true);
  const saveController = useRef<AbortController | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      saveController.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (mode !== "edit" || !promptId) {
      setDraft(emptyDraft());
      setLoading(false);
      setLoadError(null);
      return;
    }

    const controller = new AbortController();
    let current = true;
    setLoading(true);
    setLoadError(null);
    setDraft(emptyDraft());

    void api.get(promptId, controller.signal).then((prompt) => {
      if (!current) return;
      setDraft(draftFromPrompt(prompt));
      setLoading(false);
    }).catch((error: unknown) => {
      if (!current || controller.signal.aborted) return;
      if (handleAuthenticationError(error, onAuthenticationError)) return;
      setLoadError(errorMessage(error));
      setLoading(false);
    });

    return () => {
      current = false;
      controller.abort();
    };
  }, [api, mode, onAuthenticationError, promptId, retry]);

  function change<K extends keyof PromptDraft>(field: K, value: PromptDraft[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || (mode === "edit" && !promptId)) return;

    const controller = new AbortController();
    saveController.current = controller;
    setSaving(true);
    setSaveError(null);

    const input: SystemPromptInput = {
      category: draft.category,
      name: draft.name,
      system_prompt: draft.system_prompt,
      user_prompt: draft.user_prompt,
      sort_order: draft.sort_order,
      is_active: draft.is_active,
    };

    try {
      if (mode === "create") {
        await api.create(input, controller.signal);
      } else if (promptId) {
        await api.update(promptId, input, controller.signal);
      }
      if (mounted.current && !controller.signal.aborted) onSaved?.();
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

  const currentCategoryIsLegacyChoice = CATEGORIES.some((choice) => choice.value === draft.category);

  if (mode === "edit" && !promptId) {
    return (
      <section className="admin-prompts-page">
        <header className="admin-prompts-heading">
          <div><p className="admin-prompts-eyebrow">内容配置</p><h1>编辑系统提示词</h1></div>
          <button className="admin-button" type="button" onClick={onCancel}>返回列表</button>
        </header>
        <p className="admin-prompts-state" role="alert">缺少提示词编号。</p>
      </section>
    );
  }

  return (
    <section className="admin-prompts-page">
      <header className="admin-prompts-heading">
        <div>
          <p className="admin-prompts-eyebrow">内容配置</p>
          <h1>{mode === "create" ? "新建系统提示词" : "编辑系统提示词"}</h1>
          <p>配置系统提示词的分类、内容和启用状态。</p>
        </div>
        <button className="admin-button" type="button" onClick={onCancel}>返回列表</button>
      </header>

      {loading ? (
        <section className="admin-prompts-state" role="status">正在读取提示词…</section>
      ) : loadError ? (
        <section className="admin-prompts-state" role="alert">
          <p>{loadError}</p>
          <button className="admin-button" type="button" onClick={() => setRetry((value) => value + 1)}>
            重试
          </button>
        </section>
      ) : (
        <form
          className="admin-prompts-form"
          onSubmit={(event) => { void submit(event); }}
        >
          <label className="admin-prompts-field">
            <span>分类</span>
            <select value={draft.category} onChange={(event) => change("category", event.target.value)}>
              <option value="">请选择分类</option>
              {!currentCategoryIsLegacyChoice && draft.category ? (
                <option value={draft.category}>{draft.category}（当前值）</option>
              ) : null}
              {CATEGORIES.map((choice) => (
                <option key={choice.value} value={choice.value}>{choice.label}</option>
              ))}
            </select>
          </label>
          <label className="admin-prompts-field">
            <span>名称</span>
            <input
              type="text"
              value={draft.name}
              onChange={(event) => change("name", event.target.value)}
            />
          </label>
          <label className="admin-prompts-field">
            <span>系统提示词</span>
            <textarea
              rows={4}
              value={draft.system_prompt}
              onChange={(event) => change("system_prompt", event.target.value)}
            />
          </label>
          <label className="admin-prompts-field">
            <span>用户提示词</span>
            <textarea
              rows={4}
              value={draft.user_prompt}
              onChange={(event) => change("user_prompt", event.target.value)}
            />
          </label>
          <label className="admin-prompts-field">
            <span>排序权重</span>
            <input
              type="number"
              value={draft.sort_order}
              onChange={(event) => change("sort_order", Number(event.target.value))}
            />
          </label>
          <label className="admin-prompts-checkbox">
            <input
              type="checkbox"
              checked={draft.is_active}
              onChange={(event) => change("is_active", event.target.checked)}
            />
            <span>启用</span>
          </label>

          {saveError ? <p className="admin-prompts-form-error" role="alert">{saveError}</p> : null}
          <div className="admin-prompts-form-actions">
            <button className="admin-button" type="button" onClick={onCancel}>取消</button>
            <button className="admin-button admin-button-primary" type="submit" disabled={saving}>
              {saving ? "保存中…" : "保存"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

export function SystemPrompts({
  api,
  onAuthenticationError,
  mode,
  promptId,
  reloadVersion,
  onCreate,
  onEdit,
  onCancel,
  onSaved,
}: SystemPromptsProps) {
  if (mode === "list") {
    return (
      <PromptList
        api={api}
        onAuthenticationError={onAuthenticationError}
        onCreate={onCreate}
        onEdit={onEdit}
        reloadVersion={reloadVersion}
      />
    );
  }

  return (
    <PromptForm
      key={mode + ":" + (promptId ?? "")}
      api={api}
      onAuthenticationError={onAuthenticationError}
      mode={mode}
      promptId={promptId}
      onCancel={onCancel}
      onSaved={onSaved}
    />
  );
}
