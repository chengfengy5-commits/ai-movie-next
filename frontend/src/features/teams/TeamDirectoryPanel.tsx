import type { TeamDirectoryEntry } from "../../shared/api/teamManagement";

interface TeamDirectoryPanelProps {
  teams: TeamDirectoryEntry[];
  selectedTeamId: string | null;
  loading: boolean;
  disabled: boolean;
  error: string | null;
  createName: string;
  joinCode: string;
  onCreateNameChange(value: string): void;
  onJoinCodeChange(value: string): void;
  onRefresh(): void;
  onCreate(): void;
  onJoin(): void;
  onSelect(teamId: string): void;
}

export function TeamDirectoryPanel({
  teams,
  selectedTeamId,
  loading,
  disabled,
  error,
  createName,
  joinCode,
  onCreateNameChange,
  onJoinCodeChange,
  onRefresh,
  onCreate,
  onJoin,
  onSelect,
}: TeamDirectoryPanelProps) {
  return (
    <section aria-label="我的团队目录" className="team-panel team-directory-panel">
      <div className="team-panel-heading">
        <div>
          <p className="team-eyebrow">协作空间</p>
          <h2>我的团队</h2>
          <p className="team-muted">创建团队、使用邀请码加入，或打开团队成员与权限管理。</p>
        </div>
        <button className="secondary-button" disabled={loading || disabled} onClick={onRefresh} type="button">
          {loading ? "正在读取…" : "刷新团队"}
        </button>
      </div>

      {error !== null && <p className="team-error" role="alert">{error}</p>}

      <div className="team-entry-grid">
        <form className="team-entry-card" onSubmit={(event) => { event.preventDefault(); onCreate(); }}>
          <h3>创建团队</h3>
          <label htmlFor="team-create-name">团队名称</label>
          <input
            autoComplete="off"
            disabled={disabled}
            id="team-create-name"
            onChange={(event) => onCreateNameChange(event.currentTarget.value)}
            value={createName}
          />
          <p className="team-muted">服务端会去除名称首尾空白。</p>
          <button className="primary-button" disabled={disabled || [...createName].length === 0 || [...createName].length > 255} type="submit">
            创建团队
          </button>
        </form>

        <form className="team-entry-card" onSubmit={(event) => { event.preventDefault(); onJoin(); }}>
          <h3>加入团队</h3>
          <label htmlFor="team-join-code">邀请码</label>
          <input
            autoComplete="off"
            disabled={disabled}
            id="team-join-code"
            onChange={(event) => onJoinCodeChange(event.currentTarget.value)}
            value={joinCode}
          />
          <p className="team-muted">请输入 6–20 个字符；代码大小写与首尾空白由服务端处理。</p>
          <button className="primary-button" disabled={disabled || [...joinCode].length < 6 || [...joinCode].length > 20} type="submit">
            加入团队
          </button>
        </form>
      </div>

      <div className="team-list-heading">
        <h3>团队列表</h3>
        <span>{teams.length} 个团队</span>
      </div>
      {loading && teams.length === 0 ? (
        <p className="team-empty">正在读取团队名单…</p>
      ) : teams.length === 0 ? (
        <p className="team-empty">当前账号还没有加入团队。</p>
      ) : (
        <div className="team-list">
          {teams.map((team) => (
            <button
              aria-current={selectedTeamId === team.id ? "true" : undefined}
              className={selectedTeamId === team.id ? "team-list-item selected" : "team-list-item"}
              disabled={disabled}
              key={team.id}
              onClick={() => onSelect(team.id)}
              type="button"
            >
              <span className="team-list-item-main">
                <strong>{team.name || "未命名团队"}</strong>
                <small>{team.member_count} 位成员 · {team.my_role}</small>
              </span>
              <span aria-hidden="true" className="team-list-arrow">›</span>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
