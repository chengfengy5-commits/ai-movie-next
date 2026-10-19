import type { TeamSeriesRecord, TeamSeriesWriteIntent } from "../../shared/api/teamSeries";

interface TeamSeriesShareControlsProps {
  actorId: string;
  teamId: string;
  row: TeamSeriesRecord;
  disabled: boolean;
  onWrite<T>(intent: TeamSeriesWriteIntent<T>): void;
}

export function TeamSeriesShareControls({ actorId, teamId, row, disabled, onWrite }: TeamSeriesShareControlsProps) {
  if (row.user_id !== actorId) return null;

  function revokeShare(): void {
    if (disabled) return;
    if (!window.confirm(`确认将《${row.name}》从当前团队撤回并恢复为个人剧集？这会清除此剧集所有章节编辑锁。`)) return;
    onWrite({
      label: "撤回剧集分享",
      teamId,
      targetId: row.id,
      submitted: { seriesId: row.id, teamId },
      request: (port, signal) => port.unshareSeries(row.id, signal),
    });
  }

  return (
    <div className="team-series-open-actions">
      <button className="danger-button" disabled={disabled} onClick={revokeShare} type="button">
        撤回分享
      </button>
    </div>
  );
}
