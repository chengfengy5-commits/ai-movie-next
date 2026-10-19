import type { TeamDetail, TeamMember } from "../../shared/api/teamManagement";
import type {
  TeamSeriesRecord,
  TeamSeriesWriteIntent,
} from "../../shared/api/teamSeries";

interface TeamSeriesClaimControlsProps {
  team: TeamDetail | null;
  teamId: string;
  actorId: string;
  row: TeamSeriesRecord;
  members: TeamMember[];
  selectedRecipientId: string;
  disabled: boolean;
  onRecipientChange(value: string): void;
  onWrite<T>(intent: TeamSeriesWriteIntent<T>): void;
}

function memberName(member: TeamMember | undefined): string {
  return member?.username || "未命名成员";
}

export function TeamSeriesClaimControls({
  team,
  teamId,
  actorId,
  row,
  members,
  selectedRecipientId,
  disabled,
  onRecipientChange,
  onWrite,
}: TeamSeriesClaimControlsProps) {
  const isAuthor = row.user_id === actorId;
  const isClaimant = row.claimed_by === actorId;
  const isMembershipOwner = team?.my_role === "owner";
  const canManageClaim = isAuthor || isClaimant || isMembershipOwner;
  const recipients = members.filter((member) => member.user_id !== actorId);
  const recipient = recipients.find((member) => member.user_id === selectedRecipientId);

  function claim(): void {
    if (row.claimed_by !== null || disabled) return;
    if (!window.confirm(`确认认领《${row.name}》？这会将当前制作负责人设为你。`)) return;
    onWrite({
      label: "认领剧集",
      teamId,
      targetId: row.id,
      submitted: { seriesId: row.id, actorId },
      request: (api, signal) => api.claimTeamSeries(teamId, row.id, actorId, signal),
    });
  }

  function unclaim(): void {
    if (row.claimed_by === null || !canManageClaim || disabled) return;
    const current = row.claimed_by_username || "当前负责人";
    if (!window.confirm(`确认取消《${row.name}》由${current}承担的认领？现有章节编辑锁不会因此释放。`)) return;
    onWrite({
      label: "取消认领",
      teamId,
      targetId: row.id,
      submitted: { seriesId: row.id, claimedBy: row.claimed_by },
      request: (api, signal) => api.unclaimTeamSeries(teamId, row.id, signal),
    });
  }

  function transfer(): void {
    if (!canManageClaim || disabled || recipient === undefined) return;
    if (!window.confirm(`确认将《${row.name}》的制作认领转交给${memberName(recipient)}？这只转交负责人，不转移剧集所有权，也不会释放章节编辑锁。`)) return;
    const recipientId = recipient.user_id;
    onWrite({
      label: "转交认领",
      teamId,
      targetId: row.id,
      submitted: { seriesId: row.id, userId: recipientId },
      request: (api, signal) => api.transferTeamSeries(teamId, row.id, recipientId, signal),
      applyAck: () => onRecipientChange(""),
    });
  }

  return (
    <div className="team-series-claim-controls">
      {row.claimed_by === null ? (
        <>
          <span className="team-series-claim-state">尚未认领</span>
          <button className="secondary-button" disabled={disabled} onClick={claim} type="button">认领剧集</button>
        </>
      ) : (
        <span className="team-series-claim-state">
          {isClaimant ? "我负责" : `负责人：${row.claimed_by_username || "未命名成员"}`}
        </span>
      )}
      {row.claimed_by !== null && canManageClaim && (
        <button className="secondary-button" disabled={disabled} onClick={unclaim} type="button">取消认领</button>
      )}
      {canManageClaim && (
        <div className="team-series-transfer-control">
          <label htmlFor={`team-series-transfer-${row.id}`}>转交给成员</label>
          <select
            disabled={disabled || recipients.length === 0}
            id={`team-series-transfer-${row.id}`}
            onChange={(event) => onRecipientChange(event.currentTarget.value)}
            value={selectedRecipientId}
          >
            <option value="">选择团队成员</option>
            {selectedRecipientId !== "" && recipient === undefined && (
              <option disabled value={selectedRecipientId}>所选成员已不在团队中</option>
            )}
            {recipients.map((member) => (
              <option key={member.user_id} value={member.user_id}>{memberName(member)}</option>
            ))}
          </select>
          <button className="secondary-button" disabled={disabled || recipient === undefined} onClick={transfer} type="button">
            转交认领
          </button>
        </div>
      )}
      {!canManageClaim && row.claimed_by !== null && (
        <span className="team-series-claim-hint">仅认领者、剧集作者或团队拥有者可以管理负责人。</span>
      )}
    </div>
  );
}
