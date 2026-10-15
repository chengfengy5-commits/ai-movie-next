import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Chapter, CreateChapterInput, StoryboardFrame } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import {
  parseCompletedFrames,
  parseRecoveredFrames,
  type StoryboardGenerationServicePort,
  type StoryboardModelChoice,
  type StoryboardPromptChoice,
  type TwoPassQuote,
  type TwoPassQuoteInput,
} from "./contracts";
import "./storyboardGeneration.css";

interface StoryboardGenerationDialogProps {
  seriesId: string;
  seriesName: string;
  userId: string;
  services: WorkspaceServices;
  onClose(): void;
  onUnauthorized(): void;
  onCreated?(chapter: Chapter): void;
}

type GenerationPhase = "loading" | "ready" | "quoting" | "quoted" | "streaming"
  | "pending" | "preview" | "saving" | "save-unknown" | "saved" | "refresh-needed";

interface DirectoryState {
  models: StoryboardModelChoice[];
  prompts: StoryboardPromptChoice[];
}

interface RequestScope {
  seriesId: string;
  userId: string;
  services: WorkspaceServices;
  generation: StoryboardGenerationServicePort;
}

function selectedText(frame: StoryboardFrame): string {
  return typeof frame.text === "string" ? frame.text : "";
}

function chapterFramesForSave(frames: StoryboardFrame[]): CreateChapterInput["content"] {
  return frames.map((frame) => {
    const input: Record<string, unknown> = { text: frame.text };
    for (const key of ["character", "scene", "prop"] as const) {
      if (Object.hasOwn(frame, key)) input[key] = frame[key];
    }
    return input as StoryboardFrame;
  });
}

function sameSavedContent(chapter: Chapter, title: string, frames: StoryboardFrame[]): boolean {
  if (chapter.title !== title || !Array.isArray(chapter.content) || chapter.content.length !== frames.length) {
    return false;
  }
  return chapter.content.every((frame, index) => {
    const expected = frames[index];
    return expected !== undefined && typeof frame.text === "string" && frame.text === expected.text;
  });
}

function isDeterministicRejection(error: unknown): boolean {
  return error instanceof ApiError && error.kind === "http"
    && error.status !== null && error.status >= 400 && error.status < 500;
}

function newIdempotencyKey(): string {
  if (typeof crypto === "undefined" || typeof crypto.randomUUID !== "function") {
    throw new ApiError("invalid-response", "当前浏览器无法安全创建生成请求标识。");
  }
  return crypto.randomUUID();
}

export function StoryboardGenerationDialog({
  seriesId,
  seriesName,
  userId,
  services,
  onClose,
  onUnauthorized,
  onCreated,
}: StoryboardGenerationDialogProps) {
  const generation = services.storyboardGeneration;
  const scope = useMemo<RequestScope | null>(() => generation === undefined ? null : ({
    seriesId,
    userId,
    services,
    generation,
  }), [generation, seriesId, services, userId]);
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const epochRef = useRef(0);
  const controllersRef = useRef(new Set<AbortController>());
  const idempotencyKeyRef = useRef<string | null>(null);
  const taskIdRef = useRef<string | null>(null);
  const quoteRef = useRef<TwoPassQuote | null>(null);
  const saveAttemptedRef = useRef(false);
  const beforeSaveIdsRef = useRef<Set<string> | null>(null);
  const createdChapterIdRef = useRef<string | null>(null);
  const [phase, setPhase] = useState<GenerationPhase>("loading");
  const [directory, setDirectory] = useState<DirectoryState>({ models: [], prompts: [] });
  const [catalogMessage, setCatalogMessage] = useState("");
  const [rawContent, setRawContent] = useState("");
  const [chapterTitle, setChapterTitle] = useState("");
  const [modelId, setModelId] = useState("");
  const [scenePromptId, setScenePromptId] = useState("");
  const [framePromptId, setFramePromptId] = useState("");
  const [temperature, setTemperature] = useState("0.7");
  const [enableThinking, setEnableThinking] = useState(false);
  const [skipFrameGeneration, setSkipFrameGeneration] = useState(false);
  const [quote, setQuote] = useState<TwoPassQuote | null>(null);
  const [frames, setFrames] = useState<StoryboardFrame[]>([]);
  const [message, setMessage] = useState("");
  const [receivedStreamText, setReceivedStreamText] = useState(false);
  const [savedChapter, setSavedChapter] = useState<Chapter | null>(null);

  const current = useCallback((epoch: number, expectedScope: RequestScope | null = scope) => (
    epochRef.current === epoch
      && expectedScope !== null
      && scopeRef.current?.seriesId === expectedScope.seriesId
      && scopeRef.current.userId === expectedScope.userId
      && scopeRef.current.services === expectedScope.services
      && scopeRef.current.generation === expectedScope.generation
  ), [scope]);

  const runScoped = useCallback(async <T,>(
    expectedScope: RequestScope,
    epoch: number,
    operation: (signal: AbortSignal) => Promise<T>,
  ): Promise<T | undefined> => {
    const controller = new AbortController();
    controllersRef.current.add(controller);
    try {
      return await operation(controller.signal);
    } catch (error) {
      if (!current(epoch, expectedScope) || controller.signal.aborted) return undefined;
      if (error instanceof ApiError && error.status === 401) onUnauthorized();
      throw error;
    } finally {
      controllersRef.current.delete(controller);
    }
  }, [current, onUnauthorized]);

  const loadDirectory = useCallback(async (expectedScope: RequestScope, epoch: number) => {
    setPhase("loading");
    setCatalogMessage("");
    try {
      const result = await runScoped(expectedScope, epoch, async (signal) => {
        const [models, prompts] = await Promise.all([
          expectedScope.generation.listModels(signal),
          expectedScope.generation.listPrompts(signal),
        ]);
        return { models, prompts };
      });
      if (result === undefined || !current(epoch, expectedScope)) return;
      setDirectory(result);
      setModelId((selected) => result.models.some((item) => item.id === selected)
        ? selected : result.models[0]?.id ?? "");
      const scenes = result.prompts.filter((item) => item.category === "scene-segmentation");
      const storyboard = result.prompts.filter((item) => item.category === "storyboard");
      setScenePromptId((selected) => scenes.some((item) => item.id === selected)
        ? selected : scenes[0]?.id ?? "");
      setFramePromptId((selected) => storyboard.some((item) => item.id === selected)
        ? selected : storyboard[0]?.id ?? "");
      setPhase("ready");
    } catch (error) {
      if (!current(epoch, expectedScope)) return;
      setPhase("ready");
      setCatalogMessage(normalizeApiError(error).message);
    }
  }, [current, runScoped]);

  useEffect(() => {
    const epoch = epochRef.current + 1;
    epochRef.current = epoch;
    for (const controller of controllersRef.current) controller.abort();
    controllersRef.current.clear();
    idempotencyKeyRef.current = null;
    taskIdRef.current = null;
    quoteRef.current = null;
    saveAttemptedRef.current = false;
    beforeSaveIdsRef.current = null;
    createdChapterIdRef.current = null;
    setQuote(null);
    setFrames([]);
    setSavedChapter(null);
    setMessage("");
    if (scope === null) {
      setPhase("ready");
      setCatalogMessage("当前服务不支持在线 AI 分镜生成；请连接已配置的服务后再试。");
      return () => {
        epochRef.current += 1;
        for (const controller of controllersRef.current) controller.abort();
        controllersRef.current.clear();
      };
    }
    void loadDirectory(scope, epoch);
    return () => {
      if (epochRef.current === epoch) epochRef.current += 1;
      for (const controller of controllersRef.current) controller.abort();
      controllersRef.current.clear();
    };
  }, [loadDirectory, scope]);

  const scenePrompts = directory.prompts.filter((item) => item.category === "scene-segmentation");
  const framePrompts = directory.prompts.filter((item) => item.category === "storyboard");
  const quoteInputsLocked = phase === "quoting" || phase === "quoted";
  const canQuote = phase === "ready" && scope !== null && rawContent.trim() !== ""
    && modelId !== "" && scenePromptId !== ""
    && (skipFrameGeneration || framePromptId !== "");

  const requestQuote = async () => {
    if (!canQuote || scope === null) return;
    const numberTemperature = Number(temperature);
    if (!Number.isFinite(numberTemperature) || numberTemperature < 0 || numberTemperature > 2) {
      setCatalogMessage("温度需填写 0 到 2 之间的数字。");
      return;
    }
    const epoch = epochRef.current;
    const input: TwoPassQuoteInput = {
      series_id: seriesId,
      raw_content: rawContent,
      model_id: modelId,
      scene_prompt_id: scenePromptId,
      ...(skipFrameGeneration ? {} : { frame_prompt_id: framePromptId }),
      temperature: numberTemperature,
      enable_thinking: enableThinking,
      skip_frame_generation: skipFrameGeneration,
    };
    setPhase("quoting");
    setMessage("");
    try {
      const result = await runScoped(scope, epoch, (signal) => scope.generation.quote(input, signal));
      if (result === undefined || !current(epoch)) return;
      setQuote(result);
      quoteRef.current = result;
      idempotencyKeyRef.current = null;
      taskIdRef.current = null;
      saveAttemptedRef.current = false;
      setFrames([]);
      setPhase("quoted");
    } catch (error) {
      if (!current(epoch)) return;
      setCatalogMessage(normalizeApiError(error).message);
      setPhase("ready");
    }
  };

  const startGeneration = async () => {
    if (scope === null || quoteRef.current === null || idempotencyKeyRef.current !== null) return;
    let idempotencyKey: string;
    try {
      idempotencyKey = newIdempotencyKey();
    } catch (error) {
      setMessage(normalizeApiError(error).message);
      return;
    }
    idempotencyKeyRef.current = idempotencyKey;
    const epoch = epochRef.current;
    const capturedQuote = quoteRef.current;
    setPhase("streaming");
    setMessage("已提交，正在生成分镜。若连接中断，可查询同一请求状态；不会自动重复提交。");
    setReceivedStreamText(false);
    try {
      const controller = new AbortController();
      controllersRef.current.add(controller);
      let terminalEventSeen = false;
      try {
        for await (const event of scope.generation.stream(
          capturedQuote.quote_id,
          idempotencyKey,
          controller.signal,
        )) {
          if (!current(epoch)) return;
          if (event.type === "receipt") {
            taskIdRef.current = typeof event.data.task_id === "string" ? event.data.task_id : null;
          } else if (event.type === "status") {
            setMessage(typeof event.data.message === "string" ? event.data.message : "正在生成分镜。");
          } else if (event.type === "content" || event.type === "step2_output") {
            if (typeof event.data.text === "string" && event.data.text !== "") setReceivedStreamText(true);
          } else if (event.type === "frame_progress") {
            const currentFrame = event.data.current;
            const totalFrame = event.data.total;
            if (typeof currentFrame === "number" && typeof totalFrame === "number") {
              setMessage(`正在生成镜头 ${currentFrame}/${totalFrame}。`);
            }
          } else if (event.type === "pending") {
            terminalEventSeen = true;
            setPhase("pending");
            setMessage("任务仍在处理中，可查询状态恢复结果。后台任务可能继续运行。");
          } else if (event.type === "error") {
            terminalEventSeen = true;
            setPhase("pending");
            setMessage(typeof event.data.message === "string"
              ? `生成未完成：${event.data.message}。可查询任务状态。`
              : "生成未完成，可查询任务状态。");
          } else if (event.type === "complete") {
            terminalEventSeen = true;
            const completed = parseCompletedFrames(event.data.frames);
            setFrames(completed);
            setChapterTitle((existing) => existing.trim() !== "" ? existing : `${seriesName} 新章节`);
            setMessage("生成完成。确认后只保存镜头文字及角色、场景、道具字段。");
            setPhase("preview");
          }
        }
        if (current(epoch) && !terminalEventSeen) {
          setPhase("pending");
          setMessage("连接已结束，尚未确认完整结果。可查询任务状态恢复；不会重复提交。");
        }
      } finally {
        controllersRef.current.delete(controller);
      }
    } catch (error) {
      if (!current(epoch)) return;
      if (error instanceof ApiError && error.status === 401) onUnauthorized();
      setPhase("pending");
      setMessage(`${normalizeApiError(error).message} 可查询任务状态恢复；不会重复提交。`);
    }
  };

  const recoverGeneration = async () => {
    if (scope === null || idempotencyKeyRef.current === null) return;
    const epoch = epochRef.current;
    const key = idempotencyKeyRef.current;
    setMessage("正在查询已提交的生成任务。");
    try {
      const snapshot = await runScoped(scope, epoch, async (signal) => {
        const submission = await scope.generation.getSubmission(key, signal);
        const taskId = taskIdRef.current ?? submission.task_id;
        taskIdRef.current = taskId;
        const task = await scope.generation.getTask(taskId, signal);
        return { submission, task };
      });
      if (snapshot === undefined || !current(epoch)) return;
      const recovered = parseRecoveredFrames(snapshot.task.result);
      if (snapshot.task.status.toLowerCase() === "completed" && recovered !== null) {
        setFrames(recovered);
        setChapterTitle((existing) => existing.trim() !== "" ? existing : `${seriesName} 新章节`);
        setPhase("preview");
        setMessage("已从任务记录恢复完整结果，可以编辑镜头文字并保存。");
        return;
      }
      setPhase("pending");
      setMessage(snapshot.task.status === "failed"
        ? "任务已结束但没有可恢复的合法分镜；本次请求不会重新提交。"
        : `任务状态：${snapshot.task.status}（${snapshot.task.billing_status}），可稍后再次查询。`);
    } catch (error) {
      if (!current(epoch)) return;
      setPhase("pending");
      setMessage(`查询未完成：${normalizeApiError(error).message}。可以稍后重试查询。`);
    }
  };

  const updateFrameText = (index: number, text: string) => {
    setFrames((currentFrames) => currentFrames.map((frame, frameIndex) => (
      frameIndex === index ? { ...frame, text } : frame
    )));
  };

  const refreshCreatedChapter = async (chapterId: string, expectedScope: RequestScope, epoch: number) => {
    const controller = new AbortController();
    controllersRef.current.add(controller);
    try {
      const chapters = await expectedScope.services.listChapters(expectedScope.seriesId, controller.signal);
      if (!current(epoch, expectedScope)) return false;
      const chapter = chapters.find((item) => item.id === chapterId);
      if (chapter === undefined) return false;
      const assets = await expectedScope.services.listStoryboardAssets(
        expectedScope.seriesId,
        chapter.id,
        controller.signal,
      );
      if (!current(epoch, expectedScope)) return false;
      void assets;
      setSavedChapter(chapter);
      setPhase("saved");
      setMessage("章节已保存，目录和镜头素材已回读确认。");
      onCreated?.(chapter);
      return true;
    } finally {
      controllersRef.current.delete(controller);
    }
  };

  const reconcileUnknownChapter = async (expectedScope: RequestScope, epoch: number) => {
    const before = beforeSaveIdsRef.current;
    if (before === null) return false;
    const controller = new AbortController();
    controllersRef.current.add(controller);
    try {
      const chapters = await expectedScope.services.listChapters(expectedScope.seriesId, controller.signal);
      if (!current(epoch, expectedScope)) return false;
      const matches = chapters.filter((chapter) => !before.has(chapter.id)
        && sameSavedContent(chapter, chapterTitle.trim(), frames));
      if (matches.length !== 1) return false;
      const match = matches[0];
      if (match === undefined) return false;
      createdChapterIdRef.current = match.id;
      return await refreshCreatedChapter(match.id, expectedScope, epoch);
    } finally {
      controllersRef.current.delete(controller);
    }
  };

  const saveChapter = async () => {
    if (scope === null || phase !== "preview" || frames.length === 0 || saveAttemptedRef.current) return;
    const title = chapterTitle.trim();
    if (title === "") {
      setMessage("请填写新章节标题。");
      return;
    }
    const createChapter = scope.services.createChapter;
    if (createChapter === undefined) {
      setMessage("当前服务不支持创建章节；生成草稿仍保留。");
      return;
    }
    const epoch = epochRef.current;
    setPhase("saving");
    setMessage("正在核对章节目录，然后创建新章节。");
    const controller = new AbortController();
    controllersRef.current.add(controller);
    try {
      const before = await scope.services.listChapters(scope.seriesId, controller.signal);
      if (!current(epoch)) return;
      beforeSaveIdsRef.current = new Set(before.map((chapter) => chapter.id));
    } catch (error) {
      if (!current(epoch)) return;
      if (error instanceof ApiError && error.status === 401) onUnauthorized();
      setPhase("preview");
      setMessage(`保存前无法读取章节目录，尚未创建：${normalizeApiError(error).message}`);
      return;
    } finally {
      controllersRef.current.delete(controller);
    }

    const input: CreateChapterInput = {
      title,
      content: chapterFramesForSave(frames),
      order: 0,
    };
    saveAttemptedRef.current = true;
    setMessage("正在保存新章节。若结果未知，将先回读目录，不会自动重发。");
    try {
      const created = await runScoped(scope, epoch, (signal) => createChapter(scope.seriesId, input, signal));
      if (created === undefined || !current(epoch)) return;
      createdChapterIdRef.current = created.id;
      setPhase("refresh-needed");
      const refreshed = await refreshCreatedChapter(created.id, scope, epoch).catch((error: unknown) => {
        if (current(epoch) && error instanceof ApiError && error.status === 401) onUnauthorized();
        return false;
      });
      if (!refreshed && current(epoch)) {
        setPhase("refresh-needed");
        setMessage("章节已创建，但目录或素材刷新未完成。可重试回读；不会再次创建。");
      }
    } catch (error) {
      if (!current(epoch)) return;
      if (isDeterministicRejection(error)) {
        saveAttemptedRef.current = false;
        setPhase("preview");
        setMessage(`服务器拒绝了本次保存，草稿仍保留：${normalizeApiError(error).message}`);
        return;
      }
      setPhase("save-unknown");
      setMessage("保存结果尚未确认。正在只读回查目录；不会自动重复创建章节。");
      const found = await reconcileUnknownChapter(scope, epoch).catch((readError: unknown) => {
        if (current(epoch) && readError instanceof ApiError && readError.status === 401) onUnauthorized();
        return false;
      });
      if (!found && current(epoch)) {
        setPhase("save-unknown");
        setMessage("目录中未能唯一确认本次新章。保留草稿，可重新核对；不会再次创建。");
      }
    }
  };

  const retryReadback = async () => {
    if (scope === null || createdChapterIdRef.current === null) {
      if (scope !== null && phase === "save-unknown") {
        const epoch = epochRef.current;
        const found = await reconcileUnknownChapter(scope, epoch).catch((error: unknown) => {
          if (current(epoch) && error instanceof ApiError && error.status === 401) onUnauthorized();
          return false;
        });
        if (!found && current(epoch)) setMessage("仍无法唯一确认新章，保留草稿且不会自动重发。");
      }
      return;
    }
    const epoch = epochRef.current;
    const refreshed = await refreshCreatedChapter(createdChapterIdRef.current, scope, epoch).catch((error: unknown) => {
      if (current(epoch) && error instanceof ApiError && error.status === 401) onUnauthorized();
      return false;
    });
    if (!refreshed && current(epoch)) setMessage("回读仍未完成；可稍后只重试回读。");
  };

  return (
    <div className="storyboard-generation-backdrop" role="presentation">
      <section
        aria-labelledby="storyboard-generation-title"
        aria-modal="true"
        className="storyboard-generation-dialog"
        role="dialog"
      >
        <header className="storyboard-generation-header">
          <div>
            <p className="storyboard-generation-eyebrow">{seriesName}</p>
            <h2 id="storyboard-generation-title">AI 生成分镜</h2>
          </div>
          <button aria-label="关闭 AI 分镜生成" className="icon-button" onClick={onClose} type="button">×</button>
        </header>

        <div className="storyboard-generation-body">
          {catalogMessage !== "" && <p className="storyboard-generation-notice" role="alert">{catalogMessage}</p>}
          {phase === "loading" && <p aria-live="polite">正在读取可用模型和提示词…</p>}

          {!(["preview", "pending", "saving", "save-unknown", "saved", "refresh-needed"] as GenerationPhase[]).includes(phase)
            && <div className="storyboard-generation-form">
              <label>
                剧本文字
                <textarea
                  autoFocus
                  disabled={quoteInputsLocked}
                  onChange={(event) => setRawContent(event.target.value)}
                  placeholder="粘贴要生成分镜的剧本文字"
                  rows={7}
                  value={rawContent}
                />
              </label>
              <label>
                新章节标题
                <input onChange={(event) => setChapterTitle(event.target.value)} value={chapterTitle} />
              </label>
              <div className="storyboard-generation-fields">
                <label>
                  生成模型
                  <select disabled={quoteInputsLocked} onChange={(event) => setModelId(event.target.value)} value={modelId}>
                    {directory.models.map((model) => (
                      <option key={model.id} value={model.id}>{model.name}（{model.category === "reasoning" ? "推理" : "文本"}）</option>
                    ))}
                  </select>
                </label>
                <label>
                  剧情分段提示词
                  <select disabled={quoteInputsLocked} onChange={(event) => setScenePromptId(event.target.value)} value={scenePromptId}>
                    {scenePrompts.map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.name}</option>)}
                  </select>
                </label>
                {!skipFrameGeneration && <label>
                  分镜提示词
                  <select disabled={quoteInputsLocked} onChange={(event) => setFramePromptId(event.target.value)} value={framePromptId}>
                    {framePrompts.map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.name}</option>)}
                  </select>
                </label>}
                <label>
                  温度
                  <input
                    disabled={quoteInputsLocked}
                    max="2"
                    min="0"
                    onChange={(event) => setTemperature(event.target.value)}
                    step="0.1"
                    type="number"
                    value={temperature}
                  />
                </label>
              </div>
              <label className="storyboard-generation-check">
                <input checked={enableThinking} disabled={quoteInputsLocked} onChange={(event) => setEnableThinking(event.target.checked)} type="checkbox" />
                启用推理
              </label>
              <label className="storyboard-generation-check">
                <input checked={skipFrameGeneration} disabled={quoteInputsLocked} onChange={(event) => setSkipFrameGeneration(event.target.checked)} type="checkbox" />
                仅生成剧情分段
              </label>
              <p className="storyboard-generation-hint">先获取报价并确认积分，再开始生成。结果可编辑，确认保存后才会创建章节。</p>
              {phase === "quoting" && <p aria-live="polite">正在获取服务器报价…</p>}
              {phase === "streaming" && <p aria-live="polite">{message}</p>}
              {receivedStreamText && phase === "streaming" && <p className="storyboard-generation-progress">已收到生成文本，完整结果确认前不会用于预览。</p>}
              <div className="storyboard-generation-actions">
                <button disabled={phase === "loading" || phase === "quoting" || !canQuote} onClick={() => void requestQuote()} type="button">
                  {phase === "quoting" ? "获取报价中…" : "获取报价"}
                </button>
                {phase === "quoted" && quote !== null && <button className="primary" onClick={() => void startGeneration()} type="button">
                  确认报价并生成（{quote.quoted_amount} 积分）
                </button>}
              </div>
              {quote !== null && phase === "quoted" && <div className="storyboard-generation-quote" role="status">
                <strong>服务器报价：{quote.quoted_amount} 积分</strong>
                <span>{quote.model_name} · 单价 {quote.unit_price} × 倍率 {quote.multiplier}</span>
                <span>有效期至 {quote.expires_at}</span>
                <p>只有点击“确认报价并生成”后才会提交一次任务。保存章节不会再次扣费。</p>
                <button onClick={() => {
                  setQuote(null);
                  quoteRef.current = null;
                  setPhase("ready");
                }} type="button">修改输入并重新报价</button>
              </div>}
            </div>}

          {phase === "streaming" && <button className="secondary" onClick={() => {
            for (const controller of controllersRef.current) controller.abort();
            setPhase("pending");
            setMessage("已停止接收。后台任务可能继续运行，可查询状态恢复；未自动取消或重试任务。");
          }} type="button">停止接收</button>}

          {(["pending", "preview", "saving", "save-unknown", "saved", "refresh-needed"] as GenerationPhase[]).includes(phase)
            && <div className="storyboard-generation-result">
              <p aria-live="polite" className="storyboard-generation-result-message">{message}</p>
              {(phase === "pending") && <button disabled={scope === null} onClick={() => void recoverGeneration()} type="button">查询已提交任务</button>}
              {phase === "preview" && <>
                <label>
                  新章节标题
                  <input onChange={(event) => setChapterTitle(event.target.value)} value={chapterTitle} />
                </label>
                <h3>生成预览（{frames.length} 个镜头）</h3>
                {frames.map((frame, index) => (
                  <label className="storyboard-generation-frame" key={index}>
                    镜头 {index + 1} 文字
                    <textarea onChange={(event) => updateFrameText(index, event.target.value)} rows={3} value={selectedText(frame)} />
                  </label>
                ))}
                <p className="storyboard-generation-hint">保存内容包括镜头文字、角色、场景和道具，时长等其他生成信息不会保存。</p>
                <button className="primary" onClick={() => void saveChapter()} type="button">确认并创建新章节</button>
              </>}
              {(phase === "saving" || phase === "save-unknown") && <button disabled={phase === "saving"} onClick={() => void retryReadback()} type="button">只读回查，不重复创建</button>}
              {phase === "refresh-needed" && <button onClick={() => void retryReadback()} type="button">重试目录与素材回读</button>}
              {phase === "saved" && savedChapter !== null && <p role="status">已保存：{savedChapter.title}</p>}
            </div>}
        </div>
      </section>
    </div>
  );
}
