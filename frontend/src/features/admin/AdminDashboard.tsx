import { useEffect, useState } from "react";
import { ApiError } from "../../shared/api/errors";
import type { AdminApi, AdminStats } from "./adminTypes";

interface AdminDashboardProps {
  api: AdminApi;
  onUnauthorized(): void;
}

function number(value: number): string {
  return value.toLocaleString("zh-CN");
}

function StatCard({ title, value, note }: { title: string; value: string; note?: string }) {
  return (
    <article className="admin-stat-card">
      <p>{title}</p>
      <strong>{value}</strong>
      {note ? <small>{note}</small> : null}
    </article>
  );
}

function dashboardError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.kind === "network") return "无法连接管理后台服务，请检查服务后重试。";
    if (error.kind === "timeout") return "读取运营数据超时，请稍后重试。";
    if (error.kind === "invalid-response") return error.message;
    return error.detail ?? error.message;
  }
  return "读取运营数据失败，请重试。";
}

export function AdminDashboard({ api, onUnauthorized }: AdminDashboardProps) {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setLoading(true);
    setError(null);

    void api.getStats(controller.signal).then((result) => {
      if (current) setStats(result);
    }).catch((cause: unknown) => {
      if (!current || controller.signal.aborted) return;
      if (cause instanceof ApiError && (cause.status === 401 || cause.status === 403)) {
        onUnauthorized();
        return;
      }
      setError(dashboardError(cause));
    }).finally(() => {
      if (current) setLoading(false);
    });

    return () => {
      current = false;
      controller.abort();
    };
  }, [api, onUnauthorized, retry]);

  if (loading && stats === null) {
    return <section className="admin-state-card" role="status">正在读取运营数据…</section>;
  }

  if (error && stats === null) {
    return (
      <section className="admin-state-card" role="alert">
        <p>{error}</p>
        <button className="admin-button" type="button" onClick={() => setRetry((value) => value + 1)}>
          重试
        </button>
      </section>
    );
  }

  const value = stats;
  if (!value) {
    return <section className="admin-state-card">当前没有运营数据。</section>;
  }

  const premiumRate = value.users.total > 0
    ? (value.users.premium / value.users.total) * 100
    : 0;
  const taskSuccessRate = value.tasks.total > 0
    ? (value.tasks.completed / value.tasks.total) * 100
    : null;
  const balance = value.upstream.balance;
  const balanceText = balance === null
    ? "未连接"
    : Number.isFinite(Number(balance))
      ? number(Number(balance))
      : String(balance);

  return (
    <div className="admin-dashboard">
      <header className="admin-page-heading">
        <div>
          <p className="admin-eyebrow">Hao AI</p>
          <h1>运营概览</h1>
          <p>平台核心数据</p>
        </div>
        {loading ? <span className="admin-inline-status" role="status">正在更新</span> : null}
      </header>

      {error ? <p className="admin-inline-error" role="alert">{error}</p> : null}

      <section className="admin-dashboard-section" aria-labelledby="admin-core-stats">
        <h2 id="admin-core-stats">核心指标</h2>
        <div className="admin-stat-grid">
          <StatCard title="总用户" value={number(value.users.total)} note={"本月新增 " + number(value.users.new_this_month)} />
          <StatCard title="会员用户" value={number(value.users.premium)} note={"占比 " + premiumRate.toFixed(1) + "%"} />
          <StatCard title="AI 任务" value={number(value.tasks.total)} note={"本月 " + number(value.tasks.this_month)} />
          <StatCard title="剧集总数" value={number(value.content.series)} note="用户创作" />
        </div>
      </section>

      <section className="admin-dashboard-section" aria-labelledby="admin-revenue-stats">
        <h2 id="admin-revenue-stats">收入概况</h2>
        <div className="admin-stat-grid">
          <StatCard title="总收入" value="—" note="支付功能开发中" />
          <StatCard title="本月收入" value="—" note="支付功能开发中" />
          <StatCard
            title="上游积分"
            value={balanceText}
            note={balance === null ? "网关未响应" : "MuseAI 网关余额"}
          />
          <StatCard
            title="会员收入占比"
            value={premiumRate.toFixed(1) + "%"}
            note={number(value.users.premium) + " / " + number(value.users.total) + " 用户"}
          />
        </div>
      </section>

      <section className="admin-dashboard-section" aria-labelledby="admin-platform-stats">
        <h2 id="admin-platform-stats">平台数据</h2>
        <div className="admin-stat-grid">
          <StatCard title="本月新增用户" value={number(value.users.new_this_month)} />
          <StatCard title="本月消耗积分" value={number(value.credits.used_this_month)} />
          <StatCard title="任务成功率" value={taskSuccessRate === null ? "—" : taskSuccessRate.toFixed(1) + "%"} />
          <StatCard title="平台剧集" value={number(value.content.series)} />
        </div>
      </section>
    </div>
  );
}
