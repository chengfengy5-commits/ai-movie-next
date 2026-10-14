import { describe, expect, it } from "vitest";
import { serializeAdminUsersCsv } from "./userExport";
import type { AdminUserRecord } from "./usersTypes";

function user(overrides: Partial<AdminUserRecord> = {}): AdminUserRecord {
  return {
    id: "user-1",
    username: "writer",
    email: "writer@example.test",
    is_superuser: false,
    membership_type: "free",
    membership_expires_at: null,
    credits: 0,
    created_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

describe("admin user CSV export", () => {
  it("quotes commas, embedded quotes, and line breaks while preserving the complete user row", () => {
    const csv = serializeAdminUsersCsv([user({
      username: 'writer, "one"\nsecond line',
      email: "writer@example.test",
      membership_type: "gold",
      membership_expires_at: "2027-03-04T05:06:07",
    })]);

    expect(csv).toBe(
      '\uFEFF"用户 ID","用户名","邮箱","超级管理员","会员类型","会员到期时间","积分","注册时间"\r\n'
      + '"user-1","writer, ""one""\nsecond line","writer@example.test","false","gold","2027-03-04T05:06:07","0","2026-10-01T00:00:00Z"',
    );
  });

  const formulaCases = [
    ["equals", "=1+1", "'=1+1"],
    ["plus", "+1+1", "'+1+1"],
    ["minus", "-1+1", "'-1+1"],
    ["at sign", "@SUM(A1)", "'@SUM(A1)"],
    ["tab prefix", "\t=1+1", "'\t=1+1"],
    ["carriage-return prefix", "\r=1+1", "'\r=1+1"],
    ["line-feed prefix", "\n=1+1", "'\n=1+1"],
    ["space prefix", "  =1+1", "'  =1+1"],
    ["ordinary text", "ordinary text", "ordinary text"],
  ] as const;

  it.each(formulaCases)("serializes %s as a safe username cell", (_name, input, expectedUsername) => {
    const csv = serializeAdminUsersCsv([user({
      username: input,
      is_superuser: true,
      membership_type: "premium",
      membership_expires_at: null,
      credits: -7,
    })]);
    const expectedHeader = '\uFEFF"用户 ID","用户名","邮箱","超级管理员","会员类型","会员到期时间","积分","注册时间"';
    const expectedRow = '"user-1","' + expectedUsername.replaceAll('"', '""')
      + '","writer@example.test","true","premium","","-7","2026-10-01T00:00:00Z"';

    expect(csv).toBe(expectedHeader + "\r\n" + expectedRow);
  });

});
