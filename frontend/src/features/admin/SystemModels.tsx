import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../shared/api/errors";
import { AistarslabQuickConfig, type AistarslabQuickConfigExitNotice } from "./AistarslabQuickConfig";
import type { AistarslabApi, AistarslabBuiltParams } from "./aistarslabApi";
import type {
  AdminApi,
  AdminModel,
  AdminModelInput,
  AdminModelQuery,
  AdminModelUpdate,
} from "./adminTypes";

const PAGE_SIZE = 10;

const CATEGORIES = [
  { value: "text", label: "文本/推理" },
  { value: "image", label: "图片生成" },
  { value: "video", label: "视频生成" },
] as const;

type ModelCategory = (typeof CATEGORIES)[number]["value"];
interface ProviderChoice {
  value: string;
  label: string;
}

const PROVIDERS_BY_CATEGORY: Record<ModelCategory, ProviderChoice[]> = {
  text: [
    { value: "openai", label: "OpenAI" },
    { value: "deepseek", label: "DeepSeek" },
  ],
  image: [
    { value: "openai", label: "OpenAI (DALL-E)" },
    { value: "gpt-image", label: "GPT-Image (谷Club)" },
    { value: "aistarslab", label: "AIStarsLab" },
    { value: "zhangyuge", label: "章鱼哥AI" },
    { value: "museai", label: "MuseAI" },
    { value: "manxueapi", label: "满血API" },
    { value: "manxiaobai", label: "漫小白" },
  ],
  video: [
    { value: "zhangyuge", label: "章鱼哥AI" },
    { value: "manxiaobai", label: "漫小白" },
    { value: "xai", label: "xAI (Grok)" },
    { value: "museai-sd", label: "MuseAI-SD" },
    { value: "manxueapi", label: "满血API" },
    { value: "aistarslab", label: "AIStarsLab" },
  ],
};

function providersForCategory(category: string): ProviderChoice[] {
  switch (category) {
    case "image":
      return PROVIDERS_BY_CATEGORY.image;
    case "video":
      return PROVIDERS_BY_CATEGORY.video;
    default:
      return PROVIDERS_BY_CATEGORY.text;
  }
}

function providerOptionsForDraft(category: string, provider: string): ProviderChoice[] {
  const availableProviders = providersForCategory(category);
  if (availableProviders.some((choice) => choice.value === provider)) return availableProviders;
  return [
    { value: provider, label: provider ? `${provider}（当前值）` : "未设置（当前值）" },
    ...availableProviders,
  ];
}

const ALL_PROVIDERS = [
  { value: "openai", label: "OpenAI" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "gpt-image", label: "GPT-Image" },
  { value: "zhangyuge", label: "章鱼哥AI" },
  { value: "museai", label: "MuseAI" },
  { value: "museai-sd", label: "MuseAI-SD" },
  { value: "manxueapi", label: "满血API" },
  { value: "manxiaobai", label: "漫小白" },
  { value: "xai", label: "xAI (Grok)" },
  { value: "aistarslab", label: "AIStarsLab" },
];

type PageStatus = "loading" | "ready" | "error";
type ModelMode = "list" | "create" | "edit";

interface SystemModelsProps {
  api: AdminApi;
  aistarslabApi: AistarslabApi;
  adminActorId: string;
  adminSessionGeneration: number;
  onUnauthorized(): void;
  mode: ModelMode;
  modelId: string | undefined;
  reloadVersion: number;
  onCreate?(): void;
  onEdit?(id: string): void;
  onCancel?(): void;
  onSaved?(): void;
  onAistarslabExitStateChange?(owner: symbol, notice: AistarslabQuickConfigExitNotice | null): void;
}

interface QualityRateRow {
  quality: string;
  cost: string;
}

type BillingMode = "fixed" | "per_second";

interface ModelDraft {
  category: string;
  name: string;
  provider: string;
  model: string | null;
  base_url: string | null;
  cost_per_call: number;
  description: string | null;
  is_active: boolean;
  sort_order: number;
  params: string | null;
}

function emptyDraft(): ModelDraft {
  return {
    category: "text",
    name: "",
    provider: "openai",
    model: "",
    base_url: "",
    cost_per_call: 0,
    description: "",
    is_active: true,
    sort_order: 0,
    params: null,
  };
}

function draftFromModel(model: AdminModel): ModelDraft {
  return {
    category: model.category,
    name: model.name,
    provider: model.provider,
    model: model.model,
    base_url: model.base_url,
    cost_per_call: model.cost_per_call,
    description: model.description,
    is_active: model.is_active,
    sort_order: model.sort_order,
    params: model.params,
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

function asObject(value: string | null): Record<string, unknown> | null {
  if (value === null || value.trim() === "") return {};
  try {
    const parsed: unknown = JSON.parse(value);
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    return null;
  }
  return null;
}

function readQualityRows(params: string | null, mode: BillingMode): QualityRateRow[] {
  const value = asObject(params);
  const field = mode === "per_second" ? "per_second_by_quality" : "fixed_by_quality";
  const rates = value?.[field];
  if (typeof rates !== "object" || rates === null || Array.isArray(rates)) return [];
  return Object.entries(rates as Record<string, unknown>)
    .map(([quality, cost]) => ({ quality, cost: String(cost) }));
}

function writeQualityRows(
  params: string | null,
  mode: BillingMode,
  rows: QualityRateRow[],
): string | null {
  const value = asObject(params);
  if (value === null) return null;
  const field = mode === "per_second" ? "per_second_by_quality" : "fixed_by_quality";
  const rates: Record<string, number> = {};
  for (const row of rows) {
    const quality = row.quality.trim();
    if (quality) rates[quality] = Number.parseInt(row.cost, 10) || 0;
  }
  if (rows.length > 0) {
    value[field] = rates;
  } else {
    delete value[field];
  }
  return JSON.stringify(value, null, 2);
}

function resolutionOptions(params: Record<string, unknown> | null): string[] {
  const resolution = params?.resolution;
  if (typeof resolution !== "object" || resolution === null || Array.isArray(resolution)) return [];
  const options = (resolution as Record<string, unknown>).options;
  return Array.isArray(options) ? options.filter((value): value is string => typeof value === "string") : [];
}

function formatBilling(params: string | null, costPerCall: number): string {
  const value = asObject(params);
  if (!value) return "—";
  if (value.billing_mode === "per_second") {
    const rates = value.per_second_by_quality;
    if (typeof rates === "object" && rates !== null && !Array.isArray(rates)) {
      const amounts = Object.values(rates).map((item) => Number(item) || 0);
      if (amounts.length > 1) return "按秒 " + Math.min(...amounts) + "–" + Math.max(...amounts) + " 积分/秒";
    }
    return "按秒 " + (Number(value.per_second_cost) || 0) + " 积分/秒";
  }
  const rates = value.fixed_by_quality;
  if (typeof rates === "object" && rates !== null && !Array.isArray(rates)) {
    const amounts = Object.values(rates).map((item) => Number(item) || 0);
    if (amounts.length > 1) return "按次 " + Math.min(...amounts) + "–" + Math.max(...amounts) + " 积分/次";
  }
  return "按次 " + costPerCall + " 积分/次";
}

function handleUnauthorized(error: unknown, onUnauthorized: () => void): boolean {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    onUnauthorized();
    return true;
  }
  return false;
}

interface ModelListProps {
  api: AdminApi;
  onUnauthorized(): void;
  onCreate: (() => void) | undefined;
  onEdit: ((id: string) => void) | undefined;
  reloadVersion: number;
}

interface ModelListPaginationProps {
  total: number;
  page: number;
  pageCount: number;
  onPageChange(page: number): void;
}

function ModelListPagination({ total, page, pageCount, onPageChange }: ModelListPaginationProps) {
  return (
    <footer className="admin-table-footer">
      <span>共 {total.toLocaleString("zh-CN")} 个模型</span>
      <div className="admin-pagination">
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

function ModelList({
  api,
  onUnauthorized,
  onCreate,
  onEdit,
  reloadVersion,
}: ModelListProps) {
  const [category, setCategory] = useState("");
  const [provider, setProvider] = useState("");
  const [active, setActive] = useState("");
  const [page, setPage] = useState(1);
  const [retry, setRetry] = useState(0);
  const [listVersion, setListVersion] = useState(0);
  const [rows, setRows] = useState<AdminModel[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<PageStatus>("loading");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const mounted = useRef(true);
  const deleteController = useRef<AbortController | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      deleteController.current?.abort();
    };
  }, []);

  const query = useMemo<AdminModelQuery>(() => {
    const result: AdminModelQuery = { skip: (page - 1) * PAGE_SIZE, limit: PAGE_SIZE };
    if (category) result.category = category;
    if (provider) result.provider = provider;
    if (active !== "") result.is_active = active === "true";
    return result;
  }, [active, category, page, provider]);
  const latestQuery = useRef(query);
  latestQuery.current = query;

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setStatus("loading");
    setLoadError(null);
    setRows([]);

    void api.listModels(query, controller.signal).then((result) => {
      if (!current) return;
      setRows(result.data);
      setTotal(result.total);
      setStatus("ready");
    }).catch((cause: unknown) => {
      if (!current || controller.signal.aborted) return;
      if (handleUnauthorized(cause, onUnauthorized)) return;
      setLoadError(errorMessage(cause));
      setStatus("error");
    });

    return () => {
      current = false;
      controller.abort();
    };
  }, [api, onUnauthorized, query, reloadVersion, retry, listVersion]);

  function applyFilterChange(change: () => void) {
    change();
    setPage(1);
  }

  async function removeModel(model: AdminModel) {
    if (!window.confirm("确认删除模型“" + model.name + "”吗？")) return;
    const deleteQuery = query;
    const deletePage = page;
    const deletedLastRowOnPage = rows.length === 1;
    const controller = new AbortController();
    deleteController.current = controller;
    setDeletingId(model.id);
    setActionError(null);
    try {
      await api.deleteModel(model.id, controller.signal);
      if (!mounted.current || controller.signal.aborted) return;
      if (latestQuery.current === deleteQuery && deletedLastRowOnPage && deletePage > 1) {
        setPage((value) => Math.max(1, value - 1));
      } else {
        setListVersion((value) => value + 1);
      }
    } catch (cause) {
      if (!mounted.current || controller.signal.aborted) return;
      if (!handleUnauthorized(cause, onUnauthorized)) {
        setActionError(errorMessage(cause));
      }
    } finally {
      if (mounted.current && deleteController.current === controller) {
        deleteController.current = null;
        setDeletingId(null);
      }
    }
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="admin-models-page">
      <header className="admin-page-heading">
        <div>
          <p className="admin-eyebrow">模型配置</p>
          <h1>系统模型</h1>
          <p>管理平台使用的文本、图片和视频模型。</p>
        </div>
        <button className="admin-button admin-button-primary" type="button" onClick={onCreate}>
          新建模型
        </button>
      </header>

      <section className="admin-filter-panel" aria-label="模型筛选">
        <label>
          <span>分类</span>
          <select
            value={category}
            onChange={(event) => applyFilterChange(() => setCategory(event.target.value))}
          >
            <option value="">全部分类</option>
            {CATEGORIES.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
          </select>
        </label>
        <label>
          <span>提供商</span>
          <select
            value={provider}
            onChange={(event) => applyFilterChange(() => setProvider(event.target.value))}
          >
            <option value="">全部提供商</option>
            {ALL_PROVIDERS.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
          </select>
        </label>
        <label>
          <span>启用状态</span>
          <select
            value={active}
            onChange={(event) => applyFilterChange(() => setActive(event.target.value))}
          >
            <option value="">全部</option>
            <option value="true">启用</option>
            <option value="false">禁用</option>
          </select>
        </label>
        <button
          className="admin-button admin-button-secondary"
          type="button"
          onClick={() => {
            setCategory("");
            setProvider("");
            setActive("");
            setPage(1);
          }}
        >
          重置
        </button>
        <span className="admin-sort-note">按排序值升序</span>
      </section>

      {actionError ? <p className="admin-inline-error" role="alert">{actionError}</p> : null}

      {status === "loading" ? (
        <section className="admin-state-card" role="status">正在读取模型…</section>
      ) : status === "error" ? (
        <section className="admin-state-card" role="alert">
          <p>{loadError}</p>
          <button className="admin-button" type="button" onClick={() => setRetry((value) => value + 1)}>
            重试
          </button>
        </section>
      ) : rows.length === 0 ? (
        <div className="admin-table-wrap admin-empty-list">
          <section className="admin-state-card">
            <strong>没有符合条件的模型</strong>
            <p>{total === 0 ? "调整筛选条件，或新建一个系统模型。" : "当前页没有模型记录。"}</p>
          </section>
          <ModelListPagination total={total} page={page} pageCount={pageCount} onPageChange={setPage} />
        </div>
      ) : (
        <div className="admin-table-wrap">
          <table className="admin-model-table">
            <thead>
              <tr>
                <th scope="col">名称</th>
                <th scope="col">分类</th>
                <th scope="col">提供商</th>
                <th scope="col">模型名</th>
                <th scope="col">计费方式</th>
                <th scope="col">状态</th>
                <th scope="col">排序</th>
                <th scope="col"><span className="admin-visually-hidden">操作</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((model) => (
                <tr key={model.id}>
                  <td>
                    <button className="admin-table-link" type="button" onClick={() => onEdit?.(model.id)}>
                      {model.name}
                    </button>
                  </td>
                  <td>{CATEGORIES.find((choice) => choice.value === model.category)?.label ?? model.category}</td>
                  <td>{model.provider}</td>
                  <td>{model.model || "—"}</td>
                  <td>{formatBilling(model.params, model.cost_per_call)}</td>
                  <td><span className={model.is_active ? "admin-status-pill is-on" : "admin-status-pill"}>{model.is_active ? "启用" : "禁用"}</span></td>
                  <td>{model.sort_order}</td>
                  <td className="admin-table-actions">
                    <button className="admin-text-button" type="button" onClick={() => onEdit?.(model.id)}>编辑</button>
                    <button
                      className="admin-text-button is-danger"
                      type="button"
                      disabled={deletingId !== null}
                      onClick={() => { void removeModel(model); }}
                    >
                      {deletingId === model.id ? "删除中…" : "删除"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <ModelListPagination total={total} page={page} pageCount={pageCount} onPageChange={setPage} />
        </div>
      )}
    </section>
  );
}

interface ModelFormProps {
  api: AdminApi;
  aistarslabApi: AistarslabApi;
  adminActorId: string;
  adminSessionGeneration: number;
  onUnauthorized(): void;
  mode: "create" | "edit";
  modelId: string | undefined;
  onCancel: (() => void) | undefined;
  onSaved: (() => void) | undefined;
  onAistarslabExitStateChange: ((owner: symbol, notice: AistarslabQuickConfigExitNotice | null) => void) | undefined;
}

function ModelForm({
  api,
  aistarslabApi,
  adminActorId,
  adminSessionGeneration,
  onUnauthorized,
  mode,
  modelId,
  onCancel,
  onSaved,
  onAistarslabExitStateChange,
}: ModelFormProps) {
  const [draft, setDraft] = useState<ModelDraft>(emptyDraft);
  const [qualityRows, setQualityRows] = useState<QualityRateRow[]>([]);
  const [apiKey, setApiKey] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);
  const [hasStoredApiKey, setHasStoredApiKey] = useState(false);
  const [loading, setLoading] = useState(mode === "edit");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [aistarslabWriteLocked, setAistarslabWriteLocked] = useState(false);
  const aistarslabWriteLockedRef = useRef(false);
  const [draftGeneration, setDraftGeneration] = useState(0);
  const draftGenerationRef = useRef(0);
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
    if (mode !== "edit" || !modelId) {
      setDraft(emptyDraft());
      setQualityRows([]);
      setApiKey("");
      setHasStoredApiKey(false);
      setLoading(false);
      setLoadError(null);
      return;
    }

    const controller = new AbortController();
    let current = true;
    setLoading(true);
    setLoadError(null);
    setDraft(emptyDraft());
    setQualityRows([]);
    setApiKey("");
    setHasStoredApiKey(false);

    void api.getModel(modelId, controller.signal).then((model) => {
      if (!current) return;
      const nextDraft = draftFromModel(model);
      const nextParams = asObject(nextDraft.params);
      const nextMode: BillingMode = nextParams?.billing_mode === "per_second" ? "per_second" : "fixed";
      setDraft(nextDraft);
      setQualityRows(readQualityRows(nextDraft.params, nextMode));
      setHasStoredApiKey(Boolean(model.api_key));
      setLoading(false);
    }).catch((cause: unknown) => {
      if (!current || controller.signal.aborted) return;
      if (handleUnauthorized(cause, onUnauthorized)) return;
      setLoadError(errorMessage(cause));
      setLoading(false);
    });

    return () => {
      current = false;
      controller.abort();
    };
  }, [api, mode, modelId, onUnauthorized, retry]);

  function change<K extends keyof ModelDraft>(field: K, value: ModelDraft[K]) {
    markDraftEdited();
    setDraft((current) => ({ ...current, [field]: value }));
  }

  function markDraftEdited() {
    draftGenerationRef.current += 1;
    setDraftGeneration(draftGenerationRef.current);
  }

  function changeCategory(category: string) {
    const availableProviders = providersForCategory(category);
    draftGenerationRef.current += 1;
    setDraftGeneration(draftGenerationRef.current);
    setDraft((current) => ({
      ...current,
      category,
      provider: availableProviders.some((choice) => choice.value === current.provider)
        ? current.provider
        : availableProviders[0]?.value ?? "",
    }));
  }

  function updateBilling(modeValue: "fixed" | "per_second", cost: number) {
    const params = asObject(draft.params);
    if (params === null) return;
    params.billing_mode = modeValue;
    params.per_second_cost = cost;
    if (modeValue === "per_second") {
      delete params.fixed_by_quality;
    } else {
      delete params.per_second_by_quality;
    }
    const nextParams = JSON.stringify(params, null, 2);
    change("params", nextParams);
    setQualityRows(readQualityRows(nextParams, modeValue));
  }

  function commitQualityRows(nextRows: QualityRateRow[]) {
    markDraftEdited();
    const nextParams = writeQualityRows(draft.params, billingMode, nextRows);
    if (nextParams !== null) setDraft((current) => ({ ...current, params: nextParams }));
  }

  function updateQualityRow(index: number, field: keyof QualityRateRow, value: string) {
    const nextRows = qualityRows.map((row, rowIndex) => (
      rowIndex === index ? { ...row, [field]: value } : row
    ));
    setQualityRows(nextRows);
    commitQualityRows(nextRows);
  }

  function addQualityRow() {
    const nextRows = [...qualityRows, { quality: "", cost: "" }];
    setQualityRows(nextRows);
    commitQualityRows(nextRows);
  }

  function removeQualityRow(index: number) {
    const nextRows = qualityRows.filter((_row, rowIndex) => rowIndex !== index);
    setQualityRows(nextRows);
    commitQualityRows(nextRows);
  }

  function generateQualityRows() {
    if (!paramsObject) return;
    const options = resolutionOptions(paramsObject);
    const providerConfig = paramsObject.aistarslab;
    const channel = typeof providerConfig === "object" && providerConfig !== null && !Array.isArray(providerConfig)
      ? providerConfig as Record<string, unknown>
      : {};
    const pricing = Array.isArray(channel.pricing) ? channel.pricing : [];
    let nextRows: QualityRateRow[];

    if (billingMode === "per_second") {
      const baseCost = Number.parseInt(String(channel.perSecondCredits ?? ""), 10) || 0;
      const multiplier = baseCost > 0 && billingCost > 0 ? billingCost / baseCost : 1;
      const priced = pricing.flatMap((item): QualityRateRow[] => {
        if (typeof item !== "object" || item === null || Array.isArray(item)) return [];
        const entry = item as Record<string, unknown>;
        const price = entry.pricing;
        if (typeof entry.quality !== "string" || typeof price !== "object" || price === null || Array.isArray(price)) return [];
        const detail = price as Record<string, unknown>;
        if (detail.type !== "per_second" || detail.credits === null || detail.credits === undefined) return [];
        return [{ quality: entry.quality, cost: String(Math.round((Number(detail.credits) || 0) * multiplier)) }];
      });
      nextRows = priced.length > 0
        ? priced
        : options.map((quality) => ({
            quality,
            cost: qualityRows.find((row) => row.quality === quality)?.cost ?? (billingCost ? String(billingCost) : ""),
          }));
    } else {
      const priced = pricing.flatMap((item): QualityRateRow[] => {
        if (typeof item !== "object" || item === null || Array.isArray(item)) return [];
        const entry = item as Record<string, unknown>;
        const price = entry.pricing;
        if (typeof entry.quality !== "string" || typeof price !== "object" || price === null || Array.isArray(price)) return [];
        const detail = price as Record<string, unknown>;
        if (detail.type !== "fixed_total" || detail.credits === null || detail.credits === undefined) return [];
        return [{ quality: entry.quality, cost: String(detail.credits) }];
      });
      nextRows = priced.length > 0
        ? priced
        : options.map((quality) => ({
            quality,
            cost: qualityRows.find((row) => row.quality === quality)?.cost ?? (billingCost ? String(billingCost) : ""),
          }));
    }

    setQualityRows(nextRows);
    commitQualityRows(nextRows);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || aistarslabWriteLockedRef.current) return;
    const controller = new AbortController();
    saveController.current = controller;
    setSaving(true);
    setSaveError(null);

    const common = {
      category: draft.category,
      name: draft.name,
      provider: draft.provider,
      model: draft.model ?? "",
      base_url: draft.base_url ?? "",
      cost_per_call: draft.cost_per_call,
      description: draft.description ?? "",
      is_active: draft.is_active,
      sort_order: draft.sort_order,
      params: draft.params,
    };

    try {
      if (mode === "create") {
        const input: AdminModelInput = { ...common, api_key: apiKey };
        await api.createModel(input, controller.signal);
      } else if (modelId) {
        const input: AdminModelUpdate = { ...common };
        if (apiKey !== "") input.api_key = apiKey;
        await api.updateModel(modelId, input, controller.signal);
      }
      if (mounted.current && !controller.signal.aborted) onSaved?.();
    } catch (cause) {
      if (!mounted.current || controller.signal.aborted) return;
      if (!handleUnauthorized(cause, onUnauthorized)) {
        setSaveError(errorMessage(cause));
      }
    } finally {
      if (mounted.current && saveController.current === controller) {
        saveController.current = null;
        setSaving(false);
      }
    }
  }

  const paramsObject = asObject(draft.params);
  const billingMode = paramsObject?.billing_mode === "per_second" ? "per_second" : "fixed";
  const billingCost = Number(paramsObject?.per_second_cost) || 0;
  const rateField = billingMode === "per_second" ? "per_second_by_quality" : "fixed_by_quality";
  const rates = paramsObject?.[rateField];
  const qualityRates = typeof rates === "object" && rates !== null && !Array.isArray(rates)
    ? Object.entries(rates as Record<string, unknown>)
    : [];
  const providerOptions = providerOptionsForDraft(draft.category, draft.provider);

  function applyAistarslabParams(result: AistarslabBuiltParams) {
    if (aistarslabWriteLockedRef.current) return;
    const nextName = draft.name || `AIS-${result.lineTitle || result.label}`;
    draftGenerationRef.current += 1;
    setDraftGeneration(draftGenerationRef.current);
    setDraft((current) => ({
      ...current,
      name: current.name || nextName,
      model: `${result.channel}:${result.model}`,
      params: result.params,
    }));
    const nextParams = asObject(result.params);
    const nextBillingMode: BillingMode = nextParams?.billing_mode === "per_second" ? "per_second" : "fixed";
    setQualityRows(readQualityRows(result.params, nextBillingMode));
  }

  function updateAistarslabWriteActivity(active: boolean) {
    aistarslabWriteLockedRef.current = active;
    setAistarslabWriteLocked(active);
  }

  if (loading) {
    return <section className="admin-state-card" role="status">正在读取模型详情…</section>;
  }

  if (loadError) {
    return (
      <section className="admin-state-card" role="alert">
        <p>{loadError}</p>
        <div className="admin-button-row">
          <button className="admin-button admin-button-secondary" type="button" onClick={onCancel}>返回列表</button>
          <button className="admin-button" type="button" onClick={() => setRetry((value) => value + 1)}>重试读取</button>
        </div>
      </section>
    );
  }

  if (mode === "edit" && !modelId) {
    return <section className="admin-state-card" role="alert">缺少模型编号，无法读取详情。</section>;
  }

  return (
    <section className="admin-model-form-page">
      <header className="admin-page-heading">
        <div>
          <p className="admin-eyebrow">模型配置</p>
          <h1>{mode === "create" ? "新建系统模型" : "编辑系统模型"}</h1>
          <p>{mode === "create" ? "填写模型信息并保存。" : "修改模型信息并保存。"}</p>
        </div>
      </header>

      <form className="admin-model-form" onSubmit={(event) => { void submit(event); }}>
        <fieldset className="admin-model-fields" disabled={saving || aistarslabWriteLocked}>
          <div className="admin-form-grid">
            <label className="admin-field">
              <span>模型名称</span>
              <input required value={draft.name} onChange={(event) => change("name", event.target.value)} />
            </label>
            <label className="admin-field">
              <span>分类</span>
              <select value={draft.category} onChange={(event) => changeCategory(event.target.value)}>
                {CATEGORIES.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
              </select>
            </label>
            <label className="admin-field">
              <span>提供商</span>
              <select value={draft.provider} onChange={(event) => change("provider", event.target.value)}>
                {providerOptions.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
              </select>
            </label>
            <label className="admin-field">
              <span>模型名</span>
              <input value={draft.model ?? ""} onChange={(event) => change("model", event.target.value)} placeholder="例如 gpt-4" />
            </label>

            <div className="admin-field admin-field-span">
              <label className="admin-field-label" htmlFor="admin-system-model-api-key">API Key</label>
              <span className="admin-secret-control">
                <input
                  id="admin-system-model-api-key"
                  type={showApiKey ? "text" : "password"}
                  autoComplete="new-password"
                  value={apiKey}
                  onChange={(event) => {
                    draftGenerationRef.current += 1;
                    setDraftGeneration(draftGenerationRef.current);
                    setApiKey(event.target.value);
                  }}
                  placeholder={mode === "edit" && hasStoredApiKey ? "留空会保留当前 API Key" : "请输入 API Key"}
                  aria-describedby={mode === "edit" && hasStoredApiKey ? "admin-system-model-api-key-help" : undefined}
                />
                <button className="admin-button admin-button-secondary" type="button" onClick={() => setShowApiKey((value) => !value)}>
                  {showApiKey ? "隐藏" : "显示"}
                </button>
              </span>
              {mode === "edit" && hasStoredApiKey
                ? <small id="admin-system-model-api-key-help">已配置密钥；只有输入新值时才会提交 API Key。</small>
                : null}
            </div>

            <label className="admin-field admin-field-span">
              <span>Base URL（可选）</span>
              <input value={draft.base_url ?? ""} onChange={(event) => change("base_url", event.target.value)} />
            </label>

            <label className="admin-field">
              <span>每次调用消耗积分</span>
              <input
                type="number"
                min="0"
                step="1"
                value={draft.cost_per_call}
                onChange={(event) => change("cost_per_call", Number.parseInt(event.target.value, 10) || 0)}
              />
            </label>
            <label className="admin-field">
              <span>排序（越小越前）</span>
              <input
                type="number"
                step="1"
                value={draft.sort_order}
                onChange={(event) => change("sort_order", Number.parseInt(event.target.value, 10) || 0)}
              />
            </label>

            {mode === "edit" ? (
              <fieldset className="admin-field admin-field-span admin-billing-fields">
                <legend>计费方式</legend>
                <label>
                  <span>计费模式</span>
                  <select
                    value={billingMode}
                    disabled={paramsObject === null}
                    onChange={(event) => updateBilling(event.target.value === "per_second" ? "per_second" : "fixed", billingCost)}
                  >
                    <option value="fixed">按次计费</option>
                    <option value="per_second">按秒计费</option>
                  </select>
                </label>
                <label>
                  <span>{billingMode === "per_second" ? "每秒消耗积分" : "每次消耗积分"}</span>
                  <input
                    type="number"
                    min="0"
                    step="1"
                    value={billingCost}
                    disabled={paramsObject === null}
                    onChange={(event) => updateBilling(billingMode, Number.parseInt(event.target.value, 10) || 0)}
                  />
                </label>
                <p className="admin-field-help">
                  {billingMode === "per_second"
                    ? "按秒计费：每秒积分 × 实际秒数。"
                    : "按次计费：每次调用消耗固定积分。"}
                  {qualityRates.length > 0
                    ? " 当前已配置 " + qualityRates.length + " 个清晰度档位。"
                    : " 可以按清晰度设置不同积分。"}
                </p>
                <div className="admin-quality-editor">
                  <div className="admin-quality-heading">
                    <span>{billingMode === "per_second" ? "每档每秒积分" : "每档每次积分"}</span>
                    <div>
                      {resolutionOptions(paramsObject).length > 0 ? (
                        <button className="admin-text-button" type="button" onClick={generateQualityRows}>
                          按清晰度选项生成
                        </button>
                      ) : null}
                      <button className="admin-text-button" type="button" onClick={addQualityRow}>
                        + 添加档位
                      </button>
                    </div>
                  </div>
                  {qualityRows.length === 0 ? (
                    <p className="admin-field-help">未设置分档积分；未配置的清晰度使用上方默认值。</p>
                  ) : null}
                  {qualityRows.map((row, index) => (
                    <div className="admin-quality-row" key={index}>
                      <label>
                        <span>档位名</span>
                        <input
                          value={row.quality}
                          placeholder="如 480p / 高清 / 标准"
                          onChange={(event) => updateQualityRow(index, "quality", event.target.value)}
                        />
                      </label>
                      <label>
                        <span>{billingMode === "per_second" ? "每秒积分" : "每次积分"}</span>
                        <input
                          type="number"
                          min="0"
                          step="1"
                          value={row.cost}
                          onChange={(event) => updateQualityRow(index, "cost", event.target.value)}
                        />
                      </label>
                      <button
                        className="admin-text-button is-danger"
                        type="button"
                        onClick={() => removeQualityRow(index)}
                      >
                        删除
                      </button>
                    </div>
                  ))}
                </div>
              </fieldset>
            ) : null}

            <label className="admin-field admin-field-span">
              <span>模型介绍</span>
              <textarea rows={3} value={draft.description ?? ""} onChange={(event) => change("description", event.target.value)} />
            </label>

            <label className="admin-checkbox-field">
              <input type="checkbox" checked={draft.is_active} onChange={(event) => change("is_active", event.target.checked)} />
              <span>是否启用</span>
            </label>

            <label className="admin-field admin-field-span">
              <span>模型参数配置（JSON）</span>
              <textarea
                className="admin-json-field"
                rows={9}
                spellCheck={false}
                value={draft.params ?? ""}
                onChange={(event) => {
                  const nextParams = event.target.value;
                  change("params", nextParams);
                  const nextObject = asObject(nextParams);
                  const nextMode: BillingMode = nextObject?.billing_mode === "per_second" ? "per_second" : "fixed";
                  setQualityRows(readQualityRows(nextParams, nextMode));
                }}
                placeholder="{}"
              />
              <small>可填写模型支持的参数；按次与按秒的清晰度分档也保存在这里。</small>
            </label>
          </div>
        </fieldset>

        {draft.provider === "aistarslab" && (draft.category === "image" || draft.category === "video") ? (
          <AistarslabQuickConfig
            api={aistarslabApi}
            adminApi={api}
            actorId={adminActorId}
            sessionGeneration={adminSessionGeneration}
            mode={mode}
            modelId={modelId}
            category={draft.category}
            provider={draft.provider}
            apiKey={apiKey}
            draftGeneration={draftGeneration}
            onUnauthorized={onUnauthorized}
            onApply={applyAistarslabParams}
            onCreated={() => onSaved?.()}
            onWriteActivityChange={updateAistarslabWriteActivity}
            onExitStateChange={(owner, notice) => onAistarslabExitStateChange?.(owner, notice)}
          />
        ) : null}

        {saveError ? <p className="admin-form-error" role="alert">{saveError}</p> : null}
        <div className="admin-form-actions">
          <button className="admin-button admin-button-secondary" type="button" onClick={onCancel} disabled={saving}>
            取消
          </button>
          <button className="admin-button admin-button-primary" type="submit" disabled={saving || aistarslabWriteLocked || !draft.name || !draft.provider}>
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </form>
    </section>
  );
}

export function SystemModels(props: SystemModelsProps) {
  if (props.mode === "list") {
    return (
      <ModelList
        api={props.api}
        onUnauthorized={props.onUnauthorized}
        onCreate={props.onCreate}
        onEdit={props.onEdit}
        reloadVersion={props.reloadVersion}
      />
    );
  }

  return (
    <ModelForm
      api={props.api}
      aistarslabApi={props.aistarslabApi}
      adminActorId={props.adminActorId}
      adminSessionGeneration={props.adminSessionGeneration}
      onUnauthorized={props.onUnauthorized}
      mode={props.mode}
      modelId={props.modelId}
      onCancel={props.onCancel}
      onSaved={props.onSaved}
      onAistarslabExitStateChange={props.onAistarslabExitStateChange}
    />
  );
}
