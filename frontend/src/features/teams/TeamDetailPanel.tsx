import { TEAM_PERMISSION_KEYS, type TeamDetail, type TeamInvite, type TeamInviteReceipt, type TeamPermission } from "../../shared/api/teamManagement";

export type TeamDetailTab = "members" | "invites" | "settings" | "series" | "reporting";

interface TeamDetailPanelProps {
  team: TeamDetail;
  actorId: string;
  tab: TeamDetailTab;
  detailLoading: boolean;
  seriesAvailable: boolean;
  nameDraft: string;
  inviteCountDraft: string;
  roleDrafts: Record<string, string>;
  permissionDrafts: Record<string, TeamPermission[]>;
  invites: TeamInvite[];
  inviteReceipt: TeamInviteReceipt | null;
  inviteLoading: boolean;
  locked: boolean;
  pendingLabel: string | null;
  notice: string | null;
  copyNotice: string | null;
  error: string | null;
  refreshWarning: string | null;
  onBack(): void;
  onTabChange(tab: TeamDetailTab): void;
  onNameChange(value: string): void;
  onSaveName(): void;
  onInviteCountChange(value: string): void;
  onRefreshInvites(): void;
  onCreateInvites(): void;
  onRevokeInvite(inviteId: string): void;
  onRoleChange(userId: string, role: string): void;
  onSaveRole(userId: string): void;
  onPermissionToggle(userId: string, permission: TeamPermission, checked: boolean): void;
  onSavePermissions(userId: string): void;
  onRemoveMember(userId: string): void;
  onLeave(): void;
  onDelete(): void;
  onDismissInviteReceipt(): void;
  onCopyInviteCode(code: string): void;
}

function hasPermission(team: TeamDetail, actorId: string, permission: TeamPermission): boolean {
  return team.owner_id === actorId || team.my_permissions.includes(permission);
}

function samePermissions(left: TeamPermission[], right: TeamPermission[]): boolean {
  return left.length === right.length && left.every((permission, index) => permission === right[index]);
}

export function TeamDetailPanel({
  team,
  actorId,
  tab,
  detailLoading,
  seriesAvailable,
  nameDraft,
  inviteCountDraft,
  roleDrafts,
  permissionDrafts,
  invites,
  inviteReceipt,
  inviteLoading,
  locked,
  pendingLabel,
  notice,
  copyNotice,
  error,
  refreshWarning,
  onBack,
  onTabChange,
  onNameChange,
  onSaveName,
  onInviteCountChange,
  onRefreshInvites,
  onCreateInvites,
  onRevokeInvite,
  onRoleChange,
  onSaveRole,
  onPermissionToggle,
  onSavePermissions,
  onRemoveMember,
  onLeave,
  onDelete,
  onDismissInviteReceipt,
  onCopyInviteCode,
}: TeamDetailPanelProps) {
  const isOwner = team.owner_id === actorId;
  const canManageInvites = hasPermission(team, actorId, "manage_invites");
  const canRemoveMembers = hasPermission(team, actorId, "remove_members");

  return (
    <section aria-label={`${team.name || "未命名团队"}团队详情`} className="team-panel team-detail-panel">
      <div className="team-panel-heading team-detail-heading">
        <div>
          <button className="team-back-link" onClick={onBack} type="button">‹ 返回团队列表</button>
          <p className="team-eyebrow">团队详情</p>
          <h2>{team.name || "未命名团队"}</h2>
          <p className="team-muted">{team.members.length} 位成员 · 当前角色：{team.my_role}</p>
        </div>
      </div>
      {detailLoading && <p className="team-muted" role="status">正在刷新团队资料…</p>}

      {notice !== null && <p className="team-success" role="status">{notice}</p>}
      {error !== null && <p className="team-error" role="alert">{error}</p>}
      {refreshWarning !== null && <p className="team-warning" role="status">{refreshWarning}</p>}
      {pendingLabel !== null && <p className="team-muted" role="status">正在执行：{pendingLabel}</p>}

      <div aria-label="团队管理面板" className="team-tabs" role="tablist">
        <button aria-selected={tab === "members"} className={tab === "members" ? "active" : ""} onClick={() => onTabChange("members")} role="tab" type="button">成员与权限</button>
        {canManageInvites && <button aria-selected={tab === "invites"} className={tab === "invites" ? "active" : ""} onClick={() => onTabChange("invites")} role="tab" type="button">邀请码</button>}
        <button aria-selected={tab === "settings"} className={tab === "settings" ? "active" : ""} onClick={() => onTabChange("settings")} role="tab" type="button">团队设置</button>
        {seriesAvailable && <button aria-selected={tab === "series"} className={tab === "series" ? "active" : ""} onClick={() => onTabChange("series")} role="tab" type="button">团队剧集</button>}
        <button aria-selected={tab === "reporting"} className={tab === "reporting" ? "active" : ""} onClick={() => onTabChange("reporting")} role="tab" type="button">用量与任务</button>
      </div>

      {tab === "members" && (
        <div className="team-members-list" role="tabpanel" aria-label="成员与权限">
          {team.members.map((member) => {
            const roleDraft = roleDrafts[member.user_id] ?? member.role;
            const permissionDraft = permissionDrafts[member.user_id] ?? member.permissions;
            const roleChanged = roleDraft !== member.role;
            const permissionsChanged = !samePermissions(permissionDraft, member.permissions);
            const canEditRole = isOwner && member.role !== "owner";
            const canEditPermissions = isOwner && member.role === "admin";
            const canRemove = canRemoveMembers && member.user_id !== actorId && member.role !== "owner";
            return (
              <article className="team-member-card" key={member.user_id}>
                <div className="team-member-heading">
                  <span aria-hidden="true" className="team-member-avatar">{member.username.slice(0, 1) || "?"}</span>
                  <div>
                    <strong>{member.username || "未命名成员"}</strong>
                    <small>{member.role === "owner" ? "团队拥有者" : member.role === "admin" ? "管理员" : "成员"}</small>
                  </div>
                </div>
                {canEditRole && (
                  <div className="team-member-controls">
                    <label htmlFor={`team-role-${member.user_id}`}>角色</label>
                    <select
                      disabled={locked}
                      id={`team-role-${member.user_id}`}
                      onChange={(event) => onRoleChange(member.user_id, event.currentTarget.value)}
                      value={roleDraft}
                    >
                      {!(["admin", "member"] as string[]).includes(member.role) && <option value={member.role}>{member.role}（旧角色）</option>}
                      <option value="admin">管理员</option>
                      <option value="member">成员</option>
                    </select>
                    <button className="secondary-button" disabled={locked || !roleChanged} onClick={() => onSaveRole(member.user_id)} type="button">保存角色</button>
                  </div>
                )}
                {canEditPermissions && (
                  <fieldset className="team-permissions" disabled={locked}>
                    <legend>团队权限</legend>
                    <div className="team-permission-grid">
                      {TEAM_PERMISSION_KEYS.map((permission) => (
                        <label key={permission}>
                          <input
                            checked={permissionDraft.includes(permission)}
                            onChange={(event) => onPermissionToggle(member.user_id, permission, event.currentTarget.checked)}
                            type="checkbox"
                          />
                          <span>{permissionLabel(permission)}</span>
                        </label>
                      ))}
                    </div>
                    <button className="secondary-button" disabled={!permissionsChanged} onClick={() => onSavePermissions(member.user_id)} type="button">保存权限</button>
                  </fieldset>
                )}
                {canRemove && <button className="danger-button" disabled={locked} onClick={() => onRemoveMember(member.user_id)} type="button">移除成员</button>}
              </article>
            );
          })}
        </div>
      )}

      {tab === "invites" && canManageInvites && (
        <div className="team-invites" role="tabpanel" aria-label="邀请码管理">
          <div className="team-invite-create">
            <label htmlFor="team-invite-count">生成数量</label>
            <input
              disabled={locked}
              id="team-invite-count"
              inputMode="numeric"
              max="5"
              min="1"
              onChange={(event) => onInviteCountChange(event.currentTarget.value)}
              type="number"
              value={inviteCountDraft}
            />
            <button className="primary-button" disabled={locked || inviteReceipt !== null || !/^[1-5]$/.test(inviteCountDraft)} onClick={onCreateInvites} type="button">生成邀请码</button>
            <button className="secondary-button" disabled={locked || inviteLoading} onClick={onRefreshInvites} type="button">{inviteLoading ? "正在读取…" : "读取邀请码"}</button>
          </div>

          {inviteReceipt !== null && (
            <div className="team-invite-receipt" role="status">
              <div className="team-invite-receipt-heading">
                <strong>本次生成已确认 · 有效期 {inviteReceipt.ttl_hours} 小时</strong>
                <button aria-label="关闭生成回执" className="team-dismiss" onClick={onDismissInviteReceipt} type="button">×</button>
              </div>
              <ul>
                {inviteReceipt.invites.map((invite) => (
                  <li key={invite.id}>
                    <code>{invite.code}</code>
                    <div className="team-invite-receipt-meta">
                      <span>有效至 {invite.expires_at}</span>
                      <button aria-label="复制邀请码" className="secondary-button" disabled={locked} onClick={() => onCopyInviteCode(invite.code)} type="button">复制</button>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {copyNotice !== null && (
            <p className={copyNotice === "邀请码已复制。" ? "team-success" : "team-warning"} role="status">
              {copyNotice}
            </p>
          )}

          {invites.length === 0 && !inviteLoading ? <p className="team-empty">还没有读取到邀请码。</p> : (
            <div className="team-invite-list">
              {invites.map((invite) => (
                <article className="team-invite-row" key={invite.id}>
                  <div className="team-invite-row-details">
                    <code>{invite.code}</code>
                    <small>{invite.status} · 到期 {invite.expires_at}</small>
                  </div>
                  <div className="team-invite-row-actions">
                    <button aria-label="复制邀请码" className="secondary-button" disabled={locked} onClick={() => onCopyInviteCode(invite.code)} type="button">复制</button>
                    {(invite.status === "active" || invite.status === "expired") && (
                      <button className="danger-button" disabled={locked} onClick={() => onRevokeInvite(invite.id)} type="button">作废</button>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}
          <p className="team-muted">读取列表时，服务端可能会将已过期的活动邀请码更新为过期状态。</p>
        </div>
      )}

      {tab === "settings" && (
        <div className="team-settings" role="tabpanel" aria-label="团队设置">
          {isOwner ? (
            <form onSubmit={(event) => { event.preventDefault(); onSaveName(); }}>
              <label htmlFor="team-name-draft">团队名称</label>
              <input disabled={locked} id="team-name-draft" onChange={(event) => onNameChange(event.currentTarget.value)} value={nameDraft} />
              <button className="primary-button" disabled={locked || nameDraft.trim() === team.name || [...nameDraft].length > 255} type="submit">保存名称</button>
            </form>
          ) : <p className="team-muted">只有团队拥有者可以修改团队名称。</p>}
          <div className="team-danger-zone">
            {team.my_role !== "owner" && <button className="danger-button" disabled={locked} onClick={onLeave} type="button">退出团队</button>}
            {isOwner && <button className="danger-button" disabled={locked} onClick={onDelete} type="button">解散团队</button>}
          </div>
        </div>
      )}

      {tab === "series" && seriesAvailable && (
        <div aria-label="团队剧集" className="team-series-tab-anchor" role="tabpanel">
          <p className="team-muted">创建团队剧集、管理制作认领，或查看当前团队的共享剧集。</p>
        </div>
      )}
    </section>
  );
}

function permissionLabel(permission: TeamPermission): string {
  const labels: Record<TeamPermission, string> = {
    view_tasks: "查看团队任务",
    view_usage: "查看团队用量",
    manage_invites: "管理邀请码",
    remove_members: "移除成员",
    delete_series: "删除团队剧集",
    enter_claimed_series: "进入已认领剧集",
  };
  return labels[permission];
}
