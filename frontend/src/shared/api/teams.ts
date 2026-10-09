import { InvalidResponseError } from "./contracts";

export interface MyTeam {
  id: string;
  name: string;
  owner_id: string;
  created_at: string;
  member_count: number;
  my_role: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(
  record: Record<string, unknown>,
  key: keyof MyTeam,
  label: string,
  allowEmpty = false,
): string {
  if (!Object.hasOwn(record, key)) {
    throw new InvalidResponseError(`${label}缺少 ${key} 字段。`);
  }
  const value = record[key];
  if (typeof value !== "string" || (!allowEmpty && value.length === 0)) {
    throw new InvalidResponseError(`${label}中的 ${key} 字段格式无效。`);
  }
  return value;
}

function isValidIsoTimestamp(value: string): boolean {
  const match = value.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,9}))?)?(?:([zZ])|([+-])(\d{2}):?(\d{2}))?$/,
  );
  if (match === null) {
    return false;
  }

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const calendar = new Date(0);
  calendar.setUTCFullYear(year, month - 1, day);
  calendar.setUTCHours(0, 0, 0, 0);
  if (
    calendar.getUTCFullYear() !== year
    || calendar.getUTCMonth() !== month - 1
    || calendar.getUTCDate() !== day
  ) {
    return false;
  }

  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = match[6] === undefined ? 0 : Number(match[6]);
  if (hour > 23 || minute > 59 || second > 59) {
    return false;
  }
  if (match[9] !== undefined) {
    const offsetHour = Number(match[10]);
    const offsetMinute = Number(match[11]);
    if (offsetHour > 23 || offsetMinute > 59) {
      return false;
    }
  }

  const normalized = /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(value) ? value : value + "Z";
  return !Number.isNaN(Date.parse(normalized));
}

function requiredTimestamp(record: Record<string, unknown>, label: string): string {
  const value = requiredString(record, "created_at", label);
  if (!isValidIsoTimestamp(value)) {
    throw new InvalidResponseError(`${label}中的 created_at 日期格式无效。`);
  }
  return value;
}

function parseMyTeam(value: unknown, index: number): MyTeam {
  const label = `团队目录第 ${index + 1} 项`;
  if (!isRecord(value)) {
    throw new InvalidResponseError(`${label}格式无效。`);
  }

  const memberCount = value.member_count;
  if (!Object.hasOwn(value, "member_count") || typeof memberCount !== "number"
    || !Number.isSafeInteger(memberCount) || memberCount < 0) {
    throw new InvalidResponseError(`${label}中的 member_count 字段格式无效。`);
  }

  return {
    id: requiredString(value, "id", label),
    name: requiredString(value, "name", label, true),
    owner_id: requiredString(value, "owner_id", label),
    created_at: requiredTimestamp(value, label),
    member_count: memberCount,
    my_role: requiredString(value, "my_role", label, true),
  };
}

export function parseMyTeamList(value: unknown): MyTeam[] {
  if (!Array.isArray(value)) {
    throw new InvalidResponseError("服务器返回的团队目录格式无效。");
  }

  const teams = value.map(parseMyTeam);
  const ids = new Set<string>();
  for (const team of teams) {
    if (ids.has(team.id)) {
      throw new InvalidResponseError("团队目录中存在重复的团队编号。");
    }
    ids.add(team.id);
  }
  return teams;
}
