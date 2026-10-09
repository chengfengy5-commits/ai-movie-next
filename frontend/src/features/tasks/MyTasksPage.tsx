import { useEffect, useState } from "react";
import { ApiError } from "../../shared/api/errors";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import { MY_TASK_PAGE_SIZE, projectMyTask, type MyTaskPage as MyTaskPageData } from "../../shared/api/myTasks";

interface MyTasksPageProps {
  services: WorkspaceServices;
  userId: string;
  onBack(): void;
  onUnauthorized(): void;
}

interface ErrorPresentation {
  title: string;
  message: string;
  kind: string;
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

function TaskCard({ task }: { task: MyTaskPageData["tasks"][number] }) {
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
    </article>
  );
}

export function MyTasksPage({ services, userId, onBack, onUnauthorized }: MyTasksPageProps) {
  const [requestedPage, setRequestedPage] = useState(1);
  const [reloadKey, setReloadKey] = useState(0);
  const [pageData, setPageData] = useState<MyTaskPageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setLoading(true);
    setError(null);

    void services.listMyTasks(requestedPage, controller.signal).then((result) => {
      if (!current || controller.signal.aborted) {
        return;
      }
      setPageData(result);
      setLoading(false);
    }).catch((cause: unknown) => {
      if (!current || controller.signal.aborted) {
        return;
      }
      const nextError = normalizeApiError(cause);
      if (nextError.status === 401) {
        onUnauthorized();
        return;
      }
      setPageData(null);
      setError(nextError);
      setLoading(false);
    });

    return () => {
      current = false;
      controller.abort();
    };
  }, [onUnauthorized, reloadKey, requestedPage, services, userId]);

  function reread() {
    setPageData(null);
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
    setError(null);
    setLoading(true);
    setRequestedPage(page);
  }

  const totalPages = pageData === null
    ? null
    : Math.max(1, Math.ceil(pageData.total / MY_TASK_PAGE_SIZE));
  const visiblePageData = pageData?.page === requestedPage ? pageData : null;
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
        <p>积分仅为任务记录；列表不提供完整请求、结果媒体或任务操作。</p>
      </div>

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
                  {visiblePageData.tasks.map((task) => <TaskCard key={task.id} task={task} />)}
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
    </section>
  );
}
