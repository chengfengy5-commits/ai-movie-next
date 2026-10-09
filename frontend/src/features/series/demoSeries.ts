import type { Series } from "../../shared/api/contracts";

const demoUserId = "demo-user";

export const demoSeries: Series[] = Array.from({ length: 24 }, (_, index) => {
  const number = index + 1;
  const owned = number <= 12;
  const teamShared = !owned || number % 3 === 0;
  const claimedByDemo = teamShared && number % 4 === 0;
  const claimedByOther = teamShared && number % 5 === 0 && !claimedByDemo;
  const claimedBy = claimedByDemo ? demoUserId : claimedByOther ? "writer-02" : null;

  return {
    id: `demo-series-${String(number).padStart(2, "0")}`,
    user_id: owned ? demoUserId : "writer-02",
    name: [
      "雾港来信",
      "第七码头",
      "长街灯火",
      "山海有约",
      "雨夜来客",
      "旧城新雪",
    ][index % 6] + (number > 6 ? ` · ${number}` : ""),
    description: number % 2 === 0 ? "一段关于选择、信任与重新开始的故事。" : null,
    image_url: null,
    style_prompt_id: null,
    style_prompt: null,
    style_prompt_name: number % 4 === 0 ? "都市悬疑" : null,
    style_prompt_owner_name: null,
    team_id: teamShared ? "team-studio" : null,
    team_name: teamShared ? "拾光工作室" : null,
    owner_name: owned ? "演示创作者" : "林编剧",
    claimed_by: claimedBy,
    claimed_by_username: claimedByDemo ? "演示创作者" : claimedByOther ? "周编剧" : null,
    claimed_by_avatar_url: null,
    can_enter: !claimedByOther,
    created_at: `2026-09-${String(30 - (index % 20)).padStart(2, "0")}T08:00:00`,
    updated_at: `2026-10-0${1 + (index % 3)}T10:30:00`,
  };
});
