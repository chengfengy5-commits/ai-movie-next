import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError } from "../../shared/api/errors";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import { MY_TASK_PAGE_SIZE, projectMyTask, type MyTaskPage as MyTaskPageData } from "../../shared/api/myTasks";
import { TaskDetailDialog } from "./TaskDetailDialog";

interface MyTasksPageProps {
  services: WorkspaceServices;
  userId: string;
  sessionGeneration?: number;
  onBack(): void;
  onUnauthorized(): void;
}

interface ErrorPresentation {
  title: string;
  message: string;
  kind: string;
}

interface OwnedTaskPage {
  data: MyTaskPageData;
  services: WorkspaceServices;
  userId: string;
  sessionGeneration: number;
}

interface TaskListOwner {
  services: WorkspaceServices;
  userId: string;
  sessionGeneration: number;
  page: number;
  reloadKey: number;
}

const TASK_OBSERVATION_INTERVAL_MS = 3_000;
const MAX_TASK_OBSERVATION_FAILURES = 3;
const ACTIVE_TASK_STATUSES = new Set(["queued", "processing", "cancelling", "reconciling"]);

function hasActiveTasks(page: MyTaskPageData): boolean {
  return page.tasks.some((task) => ACTIVE_TASK_STATUSES.has(task.status));
}

function canRetryTaskObservation(error: ApiError): boolean {
  return error.kind === "network"
    || error.kind === "timeout"
    || (error.status !== null && error.status >= 500);
}

function presentError(error: ApiError): ErrorPresentation {
  if (error.status === 403) {
    const detail = error.detail?.toLowerCase() ?? "";
    const isMembership = ["会员", "membership", "premium", "subscription"]
      .some((word) => detail.includes(word));
    return isMembership
      ? {
          title: "当前账号暂不可查看任务",
          message: error.detail ?? "会员资格暂不可用，请稍后重试。",
          kind: "membership",
        }
      : {
          title: "没有访问权限",
          message: error.detail ?? "当前账号无权读取任务列表。",
          kind: "forbidden",
        };
  }
  if (error.status === 404) {
    return { title: "任务列表暂不可用", message: error.detail ?? "本地服务没有返回任务列表。", kind: "request" };
  }
  if (error.status === 422) {
    return { title: "任务列表请求未通过校验", message: error.detail ?? "请检查本地服务返回的数据。", kind: "request" };
  }
  if (error.status !== null && error.status >= 500) {
    return { title: "服务暂时不可用", message: "本地服务暂时无法读取任务，请重试。", kind: "request" };
  }
  if (error.kind === "timeout") {
    return { title: "请求超时", message: "本地服务响应较慢，请确认服务状态后重试。", kind: "request" };
  }
  if (error.kind === "network") {
    return { title: "无法连接本地服务", message: "请确认本地服务正在运行，然后重试。", kind: "request" };
  }
  if (error.kind === "invalid-response") {
    return { title: "服务返回的数据无法识别", message: error.message, kind: "request" };
  }
  return { title: "暂时无法读取任务", message: error.detail ?? error.message, kind: "request" };
}

function TaskCard({ task, onOpen }: {
  task: MyTaskPageData["tasks"][number];
  onOpen(task: MyTaskPageData["tasks"][number]): void;
}) {
  const presentation = projectMyTask(task);

  return (
    <article className="my-task-card" aria-label={`${presentation.type}，${presentation.status}`}>
      <div className="my-task-card-heading">
        <div>
          <p className="eyebrow">任务类型</p>
          <h2>{presentation.type}</h2>
        </div>
        <span className={`my-task-status ${task.status === "failed" ? "failed" : ""}`}>
          {presentation.status}
        </span>
      </div>

      <dl className="my-task-facts">
        <div>
          <dt>创建时间</dt>
          <dd>{presentation.createdAt}</dd>
        </div>
        <div>
          <dt>更新时间</dt>
          <dd>{presentation.updatedAt}</dd>
        </div>
        <div>
          <dt>积分记录</dt>
          <dd>{presentation.creditCost}</dd>
        </div>
        {presentation.assetName !== null && (
          <div>
            <dt>{presentation.assetType ?? "关联素材"}</dt>
            <dd>{presentation.assetName}</dd>
          </div>
        )}
        {presentation.chapterTitle !== null && (
          <div>
            <dt>关联章节</dt>
            <dd>{presentation.chapterTitle}</dd>
          </div>
        )}
        {presentation.framePosition !== null && (
          <div>
            <dt>记录位置</dt>
            <dd>{presentation.framePosition}</dd>
          </div>
        )}
        {presentation.frameCount !== null && (
          <div>
            <dt>记录范围</dt>
            <dd>{presentation.frameCount}</dd>
          </div>
        )}
      </dl>

      {presentation.progress !== null && (
        <div className="my-task-progress">
          <div className="my-task-progress-label">
            <span>记录进度</span>
            <span>{presentation.progress}</span>
          </div>
          <progress max={100} value={Number.parseInt(presentation.progress, 10)} />
          {presentation.progressMessage !== null && <p>{presentation.progressMessage}</p>}
        </div>
      )}
      {presentation.progress === null && presentation.progressMessage !== null && (
        <p className="my-task-progress-note">{presentation.progressMessage}</p>
      )}

      <div className="my-task-result">
        <span>结果记录</span>
        <p>{presentation.result}</p>
      </div>

      <div className="my-task-card-actions">
        <button
          aria-label={"查看详情：" + presentation.type}
          className="secondary-button"
          onClick={() => onOpen(task)}
          type="button"
        >
          查看详情
        </button>
      </div>
    </article>
  );
}

interface SelectedTaskDetail {
  task: MyTaskPageData["tasks"][number];
  services: WorkspaceServices;
  userId: string;
  sessionGeneration: number;
  page: number;
  openGeneration: number;
  automaticallyObserving: boolean;
}

export function MyTasksPage({
  services,
  userId,
  sessionGeneration = 0,
  onBack,
  onUnauthorized,
}: MyTasksPageProps) {
  const [requestedPage, setRequestedPage] = useState(1);
  const [reloadKey, setReloadKey] = useState(0);
  const [pageSnapshot, setPageSnapshot] = useState<OwnedTaskPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [observationNotice, setObservationNotice] = useState<string | null>(null);
  const [observationSuspended, setObservationSuspended] = useState(false);
  const [documentVisible, setDocumentVisible] = useState(
    () => typeof document === "undefined" || !document.hidden,
  );
  const [selectedTaskDetail, setSelectedTaskDetail] = useState<SelectedTaskDetail | null>(null);
  const detailOpenGenerationRef = useRef(0);
  const listReadGenerationRef = useRef(0);
  const observationFailureCountRef = useRef(0);
  const onUnauthorizedRef = useRef(onUnauthorized);
  onUnauthorizedRef.current = onUnauthorized;
  const listOwnerRef = useRef<TaskListOwner>({ services, userId, sessionGeneration, page: requestedPage, reloadKey });
  const listReadCancelRef = useRef<(() => void) | null>(null);
  const observationCancelRef = useRef<(() => void) | null>(null);
  const reportDetailObservation = useCallback((openGeneration: number, active: boolean) => {
    setSelectedTaskDetail((current) => {
      if (current === null || current.openGeneration !== openGeneration
        || current.automaticallyObserving === active) return current;
      return { ...current, automaticallyObserving: active };
    });
  }, []);

  const pageData = pageSnapshot !== null
    && pageSnapshot.services === services
    && pageSnapshot.userId === userId
    && pageSnapshot.sessionGeneration === sessionGeneration
    ? pageSnapshot.data
    : null;

  function invalidateListReads(): void {
    listReadGenerationRef.current += 1;
    listReadCancelRef.current?.();
    listReadCancelRef.current = null;
    observationCancelRef.current?.();
    observationCancelRef.current = null;
  }

  useLayoutEffect(() => {
    invalidateListReads();
    listOwnerRef.current = { services, userId, sessionGeneration, page: requestedPage, reloadKey };
    observationFailureCountRef.current = 0;
    setLoading(true);
    setError(null);
    setObservationNotice(null);
    setObservationSuspended(false);
  }, [reloadKey, requestedPage, services, sessionGeneration, userId]);

  useEffect(() => {
    const updateVisibility = () => setDocumentVisible(!document.hidden);
    document.addEventListener("visibilitychange", updateVisibility);
    return () => document.removeEventListener("visibilitychange", updateVisibility);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    const requestGeneration = listReadGenerationRef.current;
    const owner = listOwnerRef.current;
    const isCurrent = () => current
      && requestGeneration === listReadGenerationRef.current
      && owner === listOwnerRef.current;
    const cancel = () => {
      if (!current) return;
      current = false;
      controller.abort();
      if (listReadCancelRef.current === cancel) listReadCancelRef.current = null;
    };
    listReadCancelRef.current = cancel;
    setLoading(true);
    setError(null);

    void services.listMyTasks(requestedPage, controller.signal).then((result) => {
      if (!isCurrent() || controller.signal.aborted) return;
      setPageSnapshot({ data: result, services, userId, sessionGeneration });
      setLoading(false);
    }).catch((cause: unknown) => {
      if (!isCurrent() || controller.signal.aborted) return;
      const nextError = normalizeApiError(cause);
      if (nextError.status === 401) {
        onUnauthorizedRef.current();
        setLoading(false);
        return;
      }
      setPageSnapshot(null);
      setError(nextError);
      setLoading(false);
    });

    return () => {
      cancel();
    };
  }, [reloadKey, requestedPage, services, sessionGeneration, userId]);

  const currentPageData = pageData?.page === requestedPage ? pageData : null;

  useEffect(() => {
    if (
      !documentVisible
      || observationSuspended
      || selectedTaskDetail?.automaticallyObserving === true
      || currentPageData === null
      || !hasActiveTasks(currentPageData)
    ) {
      return;
    }

    let current = true;
    let timer: number | null = null;
    let controller: AbortController | null = null;
    const requestGeneration = listReadGenerationRef.current;
    const owner = listOwnerRef.current;
    const isCurrent = () => current
      && requestGeneration === listReadGenerationRef.current
      && owner === listOwnerRef.current;

    const clearTimer = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };

    const cancel = () => {
      if (!current) return;
      current = false;
      clearTimer();
      controller?.abort();
      controller = null;
      if (observationCancelRef.current === cancel) observationCancelRef.current = null;
    };
    observationCancelRef.current = cancel;

    const schedule = (delay: number) => {
      if (!isCurrent() || timer !== null || controller !== null || !documentVisible) return;
      timer = window.setTimeout(() => {
        timer = null;
        void observe();
      }, delay);
    };

    const observe = async () => {
      if (!isCurrent() || controller !== null || !documentVisible) return;
      const requestController = new AbortController();
      controller = requestController;
      try {
        const result = await services.listMyTasks(requestedPage, requestController.signal);
        if (!isCurrent() || requestController.signal.aborted) return;
        controller = null;
        observationFailureCountRef.current = 0;
        setObservationNotice(null);
        setPageSnapshot({ data: result, services, userId, sessionGeneration });
        if (hasActiveTasks(result)) schedule(TASK_OBSERVATION_INTERVAL_MS);
      } catch (cause: unknown) {
        if (!isCurrent() || requestController.signal.aborted) return;
        controller = null;
        const nextError = normalizeApiError(cause);
        if (nextError.status === 401) {
          setObservationSuspended(true);
          onUnauthorizedRef.current();
          return;
        }

        const presentation = presentError(nextError);
        if (!canRetryTaskObservation(nextError)) {
          setObservationNotice(`自动刷新已停止：${presentation.title}。${presentation.message} 当前列表已保留；可点击“重新读取”再试。`);
          setObservationSuspended(true);
          return;
        }

        observationFailureCountRef.current += 1;
        const consecutiveFailures = observationFailureCountRef.current;
        if (consecutiveFailures >= MAX_TASK_OBSERVATION_FAILURES) {
          setObservationNotice(`自动刷新连续失败 ${MAX_TASK_OBSERVATION_FAILURES} 次，已暂停。当前列表已保留；可点击“重新读取”恢复。${presentation.message}`);
          setObservationSuspended(true);
          return;
        }

        setObservationNotice(`自动刷新暂时失败（${consecutiveFailures}/${MAX_TASK_OBSERVATION_FAILURES}），当前列表已保留，稍后重试。${presentation.message}`);
        schedule(TASK_OBSERVATION_INTERVAL_MS * (2 ** consecutiveFailures));
      }
    };

    schedule(TASK_OBSERVATION_INTERVAL_MS);
    return cancel;
  }, [
    currentPageData,
    documentVisible,
    observationSuspended,
    requestedPage,
    selectedTaskDetail,
    services,
    sessionGeneration,
    userId,
  ]);

  useEffect(() => {
    setSelectedTaskDetail(null);
  }, [requestedPage, services, sessionGeneration, userId]);

  function openTaskDetail(task: MyTaskPageData["tasks"][number]): void {
    detailOpenGenerationRef.current += 1;
    setSelectedTaskDetail({
      task,
      services,
      userId,
      sessionGeneration,
      page: requestedPage,
      openGeneration: detailOpenGenerationRef.current,
      automaticallyObserving: false,
    });
  }

  function reread() {
    invalidateListReads();
    setSelectedTaskDetail(null);
    setPageSnapshot(null);
    setError(null);
    setObservationNotice(null);
    setObservationSuspended(false);
    setLoading(true);
    setReloadKey((key) => key + 1);
  }

  function refreshAfterTaskControlAttempt(taskId: string): void {
    if (selectedTaskDetail === null
      || selectedTaskDetail.task.id !== taskId
      || selectedTaskDetail.services !== services
      || selectedTaskDetail.userId !== userId
      || selectedTaskDetail.sessionGeneration !== sessionGeneration
      || selectedTaskDetail.page !== requestedPage) return;
    invalidateListReads();
    setError(null);
    setLoading(true);
    setReloadKey((key) => key + 1);
  }

  function goToPage(page: number) {
    const knownTotalPages = pageData === null
      ? null
      : Math.max(1, Math.ceil(pageData.total / MY_TASK_PAGE_SIZE));
    if (
      !Number.isSafeInteger(page)
      || page < 1
      || page === requestedPage
      || (knownTotalPages !== null && page > knownTotalPages)
    ) {
      return;
    }
    invalidateListReads();
    setSelectedTaskDetail(null);
    setError(null);
    setLoading(true);
    setRequestedPage(page);
  }

  const totalPages = pageData === null
    ? null
    : Math.max(1, Math.ceil(pageData.total / MY_TASK_PAGE_SIZE));
  const visiblePageData = currentPageData;
  const needsFirstPageAction = visiblePageData !== null
    && visiblePageData.tasks.length === 0
    && visiblePageData.page > (totalPages ?? 1);

  return (
    <section className="my-tasks-page" aria-labelledby="my-tasks-heading">
      <div className="my-tasks-heading">
        <div>
          <p className="eyebrow">本人任务记录</p>
          <h1 id="my-tasks-heading">我的任务</h1>
          <p>按服务端顺序查看当前账号的任务状态快照。</p>
        </div>
        <div className="my-tasks-actions">
          <button className="secondary-button" onClick={onBack} type="button">
            返回剧集列表
          </button>
          <button className="primary-button" onClick={reread} type="button">
            重新读取
          </button>
        </div>
      </div>

      <div className="my-tasks-readonly-note">
        <span className="readonly-pill"><span aria-hidden="true">◉</span> 只读记录</span>
        <p>这里仅显示任务状态快照；打开详情后按需读取完整文本记录。</p>
      </div>

      {observationNotice !== null && (
        <div className="my-tasks-readonly-note" aria-live="polite" role="status">
          <p>{observationNotice}</p>
        </div>
      )}

      {loading && (
        <div className="state-panel loading-panel" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          <span>正在读取任务…</span>
        </div>
      )}

      {!loading && error !== null && (() => {
        const presentation = presentError(error);
        return (
          <div className={`state-panel error-panel ${presentation.kind}`} role="alert">
            <div className="state-icon" aria-hidden="true">!</div>
            <div className="state-copy">
              <h2>{presentation.title}</h2>
              <p>{presentation.message}</p>
            </div>
          </div>
        );
      })()}

      {error === null && pageData !== null && (
        <>
          {visiblePageData !== null ? (
            <>
              <div className="my-tasks-page-meta" aria-live="polite">
                <span>第 {visiblePageData.page} 页</span>
                <span>共 {visiblePageData.total} 条任务记录</span>
                <span>每页 {visiblePageData.page_size} 条</span>
              </div>

              {visiblePageData.tasks.length === 0 ? (
                <div className="state-panel empty-panel my-tasks-empty" role="status">
                  <div className="empty-icon" aria-hidden="true">◌</div>
                  <h2>{visiblePageData.page === 1 ? "还没有任务记录" : "本页暂无任务记录"}</h2>
                  <p>{visiblePageData.page === 1 ? "任务记录会在提交后显示在这里。" : "当前页没有可显示的任务记录。"}</p>
                  {needsFirstPageAction && (
                    <button className="secondary-button" onClick={() => goToPage(1)} type="button">
                      回到第一页
                    </button>
                  )}
                </div>
              ) : (
                <div className="my-tasks-list" aria-label="任务记录列表">
                  {visiblePageData.tasks.map((task) => <TaskCard key={task.id} onOpen={openTaskDetail} task={task} />)}
                </div>
              )}
            </>
          ) : null}

          {pageData !== null && (
            <nav className="my-tasks-pagination" aria-label="任务分页">
              <button
                className="secondary-button"
                disabled={totalPages === null || requestedPage <= 1 || requestedPage > totalPages}
                onClick={() => goToPage(requestedPage - 1)}
                type="button"
              >
                上一页
              </button>
              <span aria-live="polite">
                {loading ? `正在读取第 ${requestedPage} / ${totalPages} 页` : `第 ${requestedPage} / ${totalPages} 页`}
              </span>
              <button
                className="secondary-button"
                disabled={totalPages === null || requestedPage >= totalPages}
                onClick={() => goToPage(requestedPage + 1)}
                type="button"
              >
                下一页
              </button>
            </nav>
          )}
        </>
      )}

      {selectedTaskDetail !== null
        && selectedTaskDetail.userId === userId
        && selectedTaskDetail.sessionGeneration === sessionGeneration
        && selectedTaskDetail.services === services
        && selectedTaskDetail.page === requestedPage
        && (
          <TaskDetailDialog
            key={selectedTaskDetail.openGeneration}
            onClose={() => setSelectedTaskDetail(null)}
            onAutomaticObservationChange={reportDetailObservation}
            onUnauthorized={onUnauthorized}
            onCancelAttemptMayHaveReachedServer={() => refreshAfterTaskControlAttempt(selectedTaskDetail.task.id)}
            openGeneration={selectedTaskDetail.openGeneration}
            services={selectedTaskDetail.services}
            sessionGeneration={selectedTaskDetail.sessionGeneration}
            task={selectedTaskDetail.task}
            userId={selectedTaskDetail.userId}
          />
        )}
    </section>
  );
}
