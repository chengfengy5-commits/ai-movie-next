import type { MyTeam } from "../../shared/api/teams";

export const demoTeams: MyTeam[] = [
  {
    id: "team-studio",
    name: "拾光工作室",
    owner_id: "demo-user",
    created_at: "2026-09-18T09:30:00",
    member_count: 8,
    my_role: "owner",
  },
  {
    id: "all",
    name: "拾光工作室",
    owner_id: "demo-user",
    created_at: "2026-09-19T09:30:00",
    member_count: 3,
    my_role: "writer",
  },
  {
    id: "team-empty",
    name: "暂无剧集的团队",
    owner_id: "demo-user",
    created_at: "2026-09-20T09:30:00",
    member_count: 1,
    my_role: "observer",
  },
  {
    id: "__proto__",
    name: "",
    owner_id: "demo-user",
    created_at: "2026-09-21T09:30:00",
    member_count: 0,
    my_role: "",
  },
];
